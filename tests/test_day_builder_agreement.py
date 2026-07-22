"""Agreement net for the two day builders in period.py.

period.py resolves the day priority chain twice: `_build_person_day_basic` (used by
build_week_data, the week/range views) and `_populate_single_person_day` (used by
generate_period_data, the canonical month/year path). This module pins that both
builders answer the schedule question identically for a month whose fixtures exercise
every branch of the chain: absence (full and partial, left_at and arrived_at),
week-based vacation and parental leave, shift override, swap, on-call override
(ADD and REMOVE), overtime, overtime crossing midnight, an overtime day inside a
vacation week (issue #285), and an employment boundary.

Only the fields both paths genuinely produce are compared; the pay fields (ob,
oncall_pay, ot_pay, ot_hours) exist on the canonical path only.
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
from app.core.schedule.period import build_week_data, generate_period_data
from app.database.database import (
    Absence,
    AbsenceType,
    Base,
    OnCallOverride,
    OnCallOverrideType,
    OvertimeShift,
    PersonHistory,
    RotationEra,
    ShiftOverride,
    ShiftSwap,
    SwapStatus,
    User,
    UserRole,
    WageType,
)

TEST_DB_URL = "sqlite:///file:test_day_builder_agreement_memdb?mode=memory&cache=shared&uri=true"
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

D = datetime.date
START = D(2026, 3, 1)
END = D(2026, 3, 31)


def _mk_user(uid: int, name: str, person_id: int | None) -> User:
    return User(
        id=uid,
        username=f"user{uid}",
        password_hash="x",
        name=name,
        role=UserRole.USER,
        wage=30000,
        wage_type=WageType.MONTHLY,
        person_id=person_id,
        tax_table="33",
        vacation={},
        must_change_password=0,
    )


@pytest.fixture
def agree_session(monkeypatch):
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)
    clear_schedule_cache()

    session = TestSessionLocal()
    session.query(RotationEra).delete()
    session.add(
        RotationEra(
            start_date=D(2026, 1, 2),
            end_date=None,
            rotation_length=10,
            weeks_pattern=ERA_PATTERN,
        )
    )
    session.add(_mk_user(1, "Person One", 1))
    session.add(_mk_user(2, "Person Two", 2))
    session.commit()

    yield session

    session.close()
    clear_schedule_cache()
    Base.metadata.drop_all(bind=test_engine)


def _seed_chain_fixtures(session, *, with_boundary: bool = True) -> None:
    """Seed one fixture per branch of the day priority chain, all in March 2026."""
    user = session.query(User).filter(User.id == 1).first()
    user.vacation = {"2026": [11]}  # ISO week 11 = 9-15 March
    user.parental_leave = {"2026": [13]}  # ISO week 13 = 23-29 March

    # Full-day absence and both partial-absence shapes.
    session.add(Absence(user_id=1, date=D(2026, 3, 1), absence_type=AbsenceType.SICK))
    session.add(Absence(user_id=1, date=D(2026, 3, 2), absence_type=AbsenceType.SICK, left_at="20:00"))
    session.add(Absence(user_id=1, date=D(2026, 3, 3), absence_type=AbsenceType.SICK, arrived_at="20:00"))

    # Overtime: a plain OT day, an OT day inside the vacation week (issue #285),
    # and one crossing midnight into the next day.
    session.add(
        OvertimeShift(
            user_id=1,
            date=D(2026, 3, 5),
            start_time=datetime.time(8, 0),
            end_time=datetime.time(12, 0),
            hours=4.0,
            ot_pay=0.0,
            is_extension=False,
        )
    )
    session.add(
        OvertimeShift(
            user_id=1,
            date=D(2026, 3, 12),
            start_time=datetime.time(8, 0),
            end_time=datetime.time(12, 0),
            hours=4.0,
            ot_pay=0.0,
            is_extension=False,
        )
    )
    session.add(
        OvertimeShift(
            user_id=1,
            date=D(2026, 3, 7),
            start_time=datetime.time(21, 0),
            end_time=datetime.time(5, 0),
            hours=8.0,
            ot_pay=0.0,
            is_extension=False,
        )
    )

    # On-call overrides: ADD on an OFF day, REMOVE on a rotation OC day.
    session.add(OnCallOverride(user_id=1, date=D(2026, 3, 6), override_type=OnCallOverrideType.ADD))
    session.add(OnCallOverride(user_id=1, date=D(2026, 3, 17), override_type=OnCallOverrideType.REMOVE))

    # Manual shift override.
    session.add(ShiftOverride(user_id=1, date=D(2026, 3, 20), shift_code="N1", created_by=1))

    # Accepted swap between the two positions.
    session.add(
        ShiftSwap(
            requester_id=1,
            target_id=2,
            requester_date=D(2026, 3, 19),
            target_date=D(2026, 3, 18),
            requester_shift_code="N2",
            target_shift_code="N1",
            status=SwapStatus.ACCEPTED,
        )
    )

    if with_boundary:
        # Employment boundary: position 1 ends 30 March, so 31 March is before/after employment.
        session.add(
            PersonHistory(
                user_id=1,
                person_id=1,
                name="Person One",
                username="user1",
                is_active=0,
                effective_from=D(2026, 1, 2),
                effective_to=D(2026, 3, 30),
                created_by=1,
            )
        )

    session.commit()
    clear_schedule_cache()


# Fields both builders genuinely produce. Pay fields are canonical-only.
_COMPARED = ("hours", "start", "end", "rotation_week")
_FLAGS = ("before_employment", "parental_leave")


def _shape(day: dict) -> dict:
    out = {
        "shift": day["shift"].code if day.get("shift") else None,
        "original_shift": day["original_shift"].code if day.get("original_shift") else None,
    }
    out.update({k: day.get(k) for k in _COMPARED})
    out.update({k: bool(day.get(k)) for k in _FLAGS})
    out["partial_absence"] = day.get("partial_absence") is not None
    return out


def _basic_days(session) -> dict[datetime.date, dict]:
    """Day dicts from build_week_data (the _build_person_day_basic path), keyed by date."""
    days = {}
    week = START - datetime.timedelta(days=START.weekday())
    while week <= END:
        iso = week.isocalendar()
        for day in build_week_data(iso.year, iso.week, person_id=1, session=session):
            days[day["date"]] = day
        week += datetime.timedelta(days=7)
    return days


def _canonical_days(session) -> dict[datetime.date, dict]:
    """Day dicts from generate_period_data (the _populate_single_person_day path)."""
    return {d["date"]: d for d in generate_period_data(START, END, person_id=1, session=session)}


def test_builders_agree_across_the_priority_chain(agree_session):
    _seed_chain_fixtures(agree_session)

    canonical = _canonical_days(agree_session)
    basic = _basic_days(agree_session)

    mismatches = {}
    for date, canon_day in canonical.items():
        basic_day = basic[date]
        want, got = _shape(canon_day), _shape(basic_day)
        if want != got:
            mismatches[date] = {k: (want[k], got[k]) for k in want if want[k] != got[k]}

    assert mismatches == {}, f"canonical vs basic (canonical, basic): {mismatches}"


def test_builders_agree_when_vacation_and_parental_overlap(agree_session):
    """A week flagged as both vacation and parental leave must resolve the same way in both."""
    user = agree_session.query(User).filter(User.id == 1).first()
    user.vacation = {"2026": [11]}
    user.parental_leave = {"2026": [11]}
    agree_session.commit()
    clear_schedule_cache()

    canonical = _canonical_days(agree_session)
    basic = _basic_days(agree_session)

    week = [D(2026, 3, d) for d in range(9, 16)]
    assert [_shape(canonical[d])["shift"] for d in week] == [_shape(basic[d])["shift"] for d in week]


def test_fixtures_actually_exercise_the_chain(agree_session):
    """Guards the net itself: if the fixtures stop hitting the branches, agreement is vacuous."""
    _seed_chain_fixtures(agree_session)
    days = _canonical_days(agree_session)

    assert days[D(2026, 3, 1)]["shift"].code == "SICK"
    assert days[D(2026, 3, 2)].get("partial_absence") is not None
    assert days[D(2026, 3, 3)].get("partial_absence") is not None
    assert days[D(2026, 3, 5)]["shift"].code == "OT"
    assert days[D(2026, 3, 6)]["shift"].code == "OC"
    assert days[D(2026, 3, 12)]["shift"].code == "SEM"  # OT must not override vacation (issue #285)
    assert days[D(2026, 3, 17)]["shift"].code == "OFF"
    assert days[D(2026, 3, 20)]["shift"].code == "N1"
    leave_week = [d for dt, d in days.items() if D(2026, 3, 23) <= dt <= D(2026, 3, 29)]
    assert any(d["shift"] and d["shift"].code == "LEAVE" for d in leave_week)
    assert days[D(2026, 3, 31)].get("before_employment") is True
    swapped = days[D(2026, 3, 19)]
    assert swapped["shift"].code != (swapped["original_shift"].code if swapped["original_shift"] else None)
