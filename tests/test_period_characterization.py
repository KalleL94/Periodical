"""Characterization tests for period.py day generation (A1, period.py phase).

Pins the current per-day output of generate_month_data so the breakup of the oversized
period.py functions (_populate_single_person_day, 424 lines) can be done safely. Like the
summary net, it seeds its own rotation era and monkeypatches SessionLocal so the schedule is
deterministic in any environment.
"""

import datetime
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ruff: noqa: E402
import app.database.database as db_module
from app.core.schedule import clear_schedule_cache
from app.core.schedule.core import get_shift_types
from app.core.schedule.period import build_week_data, generate_month_data, mask_days_to_employment
from app.database.database import (
    Absence,
    AbsenceType,
    Base,
    OnCallOverride,
    OnCallOverrideType,
    OvertimeShift,
    RotationEra,
    User,
    UserRole,
    WageType,
)

TEST_DB_URL = "sqlite:///file:test_period_char_memdb?mode=memory&cache=shared&uri=true"
test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False, "uri": True})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

ERA_PATTERN = {
    "1": ["OFF", "OFF", "OFF", "N3", "N3", "N3", "N3"],
    "2": ["OFF", "OC", "N3", "N3", "N3", "N3", "OFF"],
    "3": ["OFF", "OFF", "N1", "N1", "N1", "N1", "OC"],
    "4": ["OC", "OFF", "N2", "N2", "N2", "OFF", "N1"],
    "5": ["N1", "N1", "N1", "N1", "OC", "OFF", "OFF"],
    "6": ["N3", "N3", "N3", "OFF", "OFF", "OC", "N3"],
    "7": ["N3", "N3", "OFF", "OC", "N2", "N2", "N2"],
    "8": ["N2", "N2", "OFF", "OFF", "N1", "N1", "N1"],
    "9": ["N1", "N1", "OC", "OFF", "OFF", "N2", "N2"],
    "10": ["N2", "N2", "N2", "N2", "OFF", "OFF", "OFF"],
}


@pytest.fixture
def char_session(monkeypatch):
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)
    clear_schedule_cache()

    session = TestSessionLocal()
    session.query(RotationEra).delete()
    session.add(
        RotationEra(
            start_date=datetime.date(2026, 1, 2),
            end_date=None,
            rotation_length=10,
            weeks_pattern=ERA_PATTERN,
        )
    )
    session.add(
        User(
            id=1,
            username="charuser",
            password_hash="x",
            name="Characterization",
            role=UserRole.USER,
            wage=30000,
            wage_type=WageType.MONTHLY,
            person_id=1,
            tax_table="33",
            vacation={},
            must_change_password=0,
        )
    )
    session.commit()

    yield session

    session.close()
    clear_schedule_cache()
    Base.metadata.drop_all(bind=test_engine)


def test_month_day_count_and_first_day(char_session):
    days = generate_month_data(2026, 3, 1, session=char_session)

    assert len(days) == 31

    first = days[0]
    assert first["date"] == datetime.date(2026, 3, 1)
    assert first["shift"].code == "N2"
    assert first["original_shift"].code == "N2"
    assert first["hours"] == 8.5
    # Name resolution (the block being refactored) returns the position holder's name.
    assert first["person_name"] == "Characterization"


def test_month_total_scheduled_hours(char_session):
    days = generate_month_data(2026, 3, 1, session=char_session)
    assert round(sum(d.get("hours", 0.0) for d in days), 2) == 216.5


def test_every_day_has_person_name(char_session):
    days = generate_month_data(2026, 3, 1, session=char_session)
    assert all(d["person_name"] == "Characterization" for d in days)


def _day(days, d: datetime.date) -> dict:
    return next(x for x in days if x["date"] == d)


def test_full_day_sick_renders_absence_shift(char_session):
    # 2026-03-01 is an N2 work day; a full sick day renders the SICK shift with no hours.
    char_session.add(Absence(user_id=1, date=datetime.date(2026, 3, 1), absence_type=AbsenceType.SICK))
    char_session.commit()

    day = _day(generate_month_data(2026, 3, 1, session=char_session), datetime.date(2026, 3, 1))

    assert day["shift"].code == "SICK"
    assert day["hours"] == 0.0


def test_partial_absence_renders_worked_portion(char_session):
    # Leaving an 8.5h N2 shift at 20:00 leaves 6h worked, keeping the original shift.
    char_session.add(Absence(user_id=1, date=datetime.date(2026, 3, 2), absence_type=AbsenceType.SICK, left_at="20:00"))
    char_session.commit()

    day = _day(generate_month_data(2026, 3, 1, session=char_session), datetime.date(2026, 3, 2))

    assert day["shift"].code == "N2"
    assert day["hours"] == 6.0
    assert "partial_absence" in day


def test_week_based_parental_leave_renders_leave(char_session):
    # Week-based parental leave (User.parental_leave JSON) renders LEAVE only on scheduled
    # (non-OFF) days of the ISO week; OFF days stay OFF.
    user = char_session.query(User).filter(User.id == 1).first()
    user.parental_leave = {"2026": [11]}  # ISO week 11 = 9-15 March 2026
    char_session.commit()

    days = build_week_data(2026, 11, person_id=1, session=char_session)

    saw_off = saw_leave = False
    for d in days:
        if d["original_shift"] and d["original_shift"].code == "OFF":
            assert d["shift"].code == "OFF"
            saw_off = True
        else:
            assert d["shift"].code == "LEAVE"
            assert d["hours"] == 0.0
            saw_leave = True
    assert saw_off and saw_leave


def test_week_based_vacation_renders_sem(char_session):
    # Week-based vacation (User.vacation JSON) renders the SEM shift only on scheduled
    # (non-OFF) days of the ISO week; OFF days stay OFF.
    user = char_session.query(User).filter(User.id == 1).first()
    user.vacation = {"2026": [11]}
    char_session.commit()

    days = build_week_data(2026, 11, person_id=1, session=char_session)

    saw_off = saw_sem = False
    for d in days:
        if d["original_shift"] and d["original_shift"].code == "OFF":
            assert d["shift"].code == "OFF"
            saw_off = True
        else:
            assert d["shift"].code == "SEM"
            assert d["hours"] == 0.0
            saw_sem = True
    assert saw_off and saw_sem


def test_oncall_pay_per_day(char_session):
    # The rotation has three OC days in March 2026; their on-call pay sums to a fixed total.
    days = generate_month_data(2026, 3, 1, session=char_session)
    oncall_days = [d for d in days if d["oncall_pay"] > 0]

    assert len(oncall_days) == 3
    assert round(sum(d["oncall_pay"] for d in days), 2) == 6082.0


def test_overtime_day_renders_ot_shift_and_pay(char_session):
    # A 4h non-extension OT shift renders the OT display shift and pays wage/72 per hour.
    char_session.add(
        OvertimeShift(
            user_id=1,
            date=datetime.date(2026, 3, 5),
            start_time=datetime.time(8, 0),
            end_time=datetime.time(12, 0),
            hours=4.0,
            ot_pay=0.0,
            is_extension=False,
        )
    )
    char_session.commit()

    day = _day(generate_month_data(2026, 3, 1, session=char_session), datetime.date(2026, 3, 5))

    assert day["shift"].code == "OT"
    assert day["ot_hours"] == 4.0
    assert round(day["ot_pay"], 2) == 1666.67


def test_oncall_override_add_creates_oc_day(char_session):
    # Manually adding on-call on an OFF day turns it into an OC shift with on-call pay.
    char_session.add(OnCallOverride(user_id=1, date=datetime.date(2026, 3, 6), override_type=OnCallOverrideType.ADD))
    char_session.commit()

    day = _day(generate_month_data(2026, 3, 1, session=char_session), datetime.date(2026, 3, 6))

    assert day["shift"].code == "OC"
    assert day["oncall_pay"] > 0


def test_oncall_override_remove_clears_oc_day(char_session):
    # Removing on-call on a rotation OC day turns it into OFF with no on-call pay.
    char_session.add(
        OnCallOverride(user_id=1, date=datetime.date(2026, 3, 17), override_type=OnCallOverrideType.REMOVE)
    )
    char_session.commit()

    day = _day(generate_month_data(2026, 3, 1, session=char_session), datetime.date(2026, 3, 17))

    assert day["shift"].code == "OFF"
    assert day["oncall_pay"] == 0.0


def test_days_after_employment_end_render_off(char_session):
    from app.database.database import PersonHistory

    char_session.add(
        PersonHistory(
            user_id=1,
            person_id=1,
            name="Characterization",
            username="charuser",
            is_active=0,
            effective_from=datetime.date(2026, 1, 2),
            effective_to=datetime.date(2026, 3, 10),
            created_by=1,
        )
    )
    char_session.commit()
    clear_schedule_cache()

    days = generate_month_data(2026, 3, person_id=1, session=char_session)

    after = [d for d in days if d["date"] > datetime.date(2026, 3, 10)]
    assert after, "expected days after the employment end in March"
    for day in after:
        assert day.get("before_employment") is True
        assert day["hours"] == 0.0
        assert day["shift"] is None or day["shift"].code == "OFF"

    on_or_before = [d for d in days if d["date"] <= datetime.date(2026, 3, 10)]
    assert any(d["shift"] and d["shift"].code not in ("OFF",) for d in on_or_before)


def test_build_week_data_basic(char_session):
    # build_week_data feeds _build_person_day_basic (coworker matching); pin its per-day output.
    days = build_week_data(2026, 11, person_id=1, session=char_session)

    assert len(days) == 7
    assert [d["shift"].code for d in days] == ["OFF", "OFF", "OFF", "N3", "N3", "N3", "N3"]
    assert all(d["person_name"] == "Characterization" for d in days)
    assert all(d["rotation_length"] == 10 for d in days)


def test_mask_days_to_employment_zeroes_days_outside_segment():
    # Days inside the segment pass through untouched; days outside render OFF with
    # every pay/hour key zeroed so summaries contribute nothing for them.
    off = next((s for s in get_shift_types() if s.code == "OFF"), None)
    n1 = next((s for s in get_shift_types() if s.code == "N1"), None)
    assert off is not None and n1 is not None

    def _work_day(d):
        return {
            "date": d,
            "person_id": 3,
            "person_name": "Anna",
            "weekday_name": "Mon",
            "shift": n1,
            "original_shift": n1,
            "rotation_week": 1,
            "hours": 8.0,
            "start": datetime.datetime.combine(d, datetime.time(8, 0)),
            "end": datetime.datetime.combine(d, datetime.time(16, 0)),
            "ob": {"OB1": 2.0},
            "oncall_pay": 100.0,
            "oncall_details": {"total_hours": 5.0},
            "ot_pay": 50.0,
            "ot_hours": 1.0,
            "ot_details": {"start_time": "16:00"},
            "ob_hours_override": None,
            "parental_leave": True,
            "partial_absence": {"hours": 4.0},
        }

    inside = _work_day(datetime.date(2026, 8, 10))
    outside = _work_day(datetime.date(2026, 8, 20))
    masked = mask_days_to_employment([inside, outside], datetime.date(2026, 8, 1), datetime.date(2026, 8, 14))

    # Inside day is the same object, unchanged.
    assert masked[0] is inside
    assert masked[0]["hours"] == 8.0

    # Outside day is a copy (original not mutated) rendered as OFF with zeroed pay.
    assert outside["hours"] == 8.0  # original untouched
    m = masked[1]
    assert m is not outside
    assert m["shift"].code == "OFF"
    assert m["hours"] == 0.0
    assert m["start"] is None and m["end"] is None
    assert m["ob"] == {}
    assert m["oncall_pay"] == 0.0 and m["oncall_details"] == {}
    assert m["ot_pay"] == 0.0 and m["ot_hours"] == 0.0 and m["ot_details"] == {}
    assert m["before_employment"] is True
    # Week-based flags the summary counts independently of the shift are cleared,
    # so a masked day contributes no parental/partial-absence total.
    assert m["parental_leave"] is False
    assert m["partial_absence"] is None
    # Identity keys are preserved.
    assert m["date"] == datetime.date(2026, 8, 20)
    assert m["person_id"] == 3
    assert m["rotation_week"] == 1
