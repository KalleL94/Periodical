"""Characterization tests for summarize_month_for_person (audit item A1).

These pin the CURRENT behaviour of the monthly pay summary so the planned breakup of the
oversized payroll functions can be done safely: any refactor that changes a computed total
fails here.

The test is fully self-contained: it seeds its own rotation era and monkeypatches
SessionLocal so the schedule does not depend on the ambient database. determine_shift_for_date
reads rotation eras via SessionLocal (not the passed session), so we point it at this test DB
and clear the schedule cache around each test to avoid cross-test pollution.
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
from app.core.schedule.core import determine_shift_for_date
from app.core.schedule.summary import (
    _apply_absence_info_to_totals,
    _hourly_corrected_gross,
    summarize_month_for_person,
)
from app.database.database import (
    Absence,
    AbsenceType,
    Base,
    OvertimeShift,
    RotationEra,
    User,
    UserRole,
    WageType,
)

YEAR = 2026
MONTH = 3
PERSON_ID = 1
WAGE = 30000

# Shared-cache in-memory DB so SessionLocal() connections all see the seeded rotation era.
TEST_DB_URL = "sqlite:///file:test_summary_char_memdb?mode=memory&cache=shared&uri=true"
test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False, "uri": True})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

# Production-shaped 10-week rotation, seeded so the schedule is deterministic in any environment.
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

EXPECTED_KEYS = {
    "year",
    "month",
    "person_id",
    "person_name",
    "total_hours",
    "num_shifts",
    "ob_hours",
    "ob_pay",
    "oncall_pay",
    "oncall_hours",
    "ot_pay",
    "absence_deduction",
    "absence_hours",
    "brutto_pay",
    "netto_pay",
    "sick_days",
    "sick_hours",
    "vab_days",
    "leave_days",
    "parental_days",
    "days",
}


@pytest.fixture
def char_session(monkeypatch):
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)
    clear_schedule_cache()  # drop any rotation cache from earlier tests

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
            wage=WAGE,
            wage_type=WageType.MONTHLY,
            person_id=PERSON_ID,
            tax_table="33",
            vacation={},
            must_change_password=0,
        )
    )
    session.commit()

    yield session

    session.close()
    clear_schedule_cache()  # don't leak the seeded era to other tests
    Base.metadata.drop_all(bind=test_engine)


def test_summary_golden_master(char_session):
    s = summarize_month_for_person(YEAR, MONTH, PERSON_ID, session=char_session, wage_user_id=1)

    # Golden values captured from the seeded rotation era above.
    assert s["total_hours"] == 144.5
    assert s["num_shifts"] == 17
    assert s["oncall_pay"] == 6082.0
    assert s["ot_pay"] == 0.0
    assert s["brutto_pay"] == 44207.0
    assert s["netto_pay"] == 34353.0
    assert s["absence_deduction"] == 0.0
    assert s["sick_days"] == 0


def test_summary_structure_and_invariants(char_session):
    s = summarize_month_for_person(YEAR, MONTH, PERSON_ID, session=char_session, wage_user_id=1)

    assert EXPECTED_KEYS.issubset(s.keys())
    assert isinstance(s["ob_pay"], dict)
    assert isinstance(s["ob_hours"], dict)
    assert s["total_hours"] > 0
    assert s["num_shifts"] > 0
    assert s["netto_pay"] <= s["brutto_pay"]


def test_hourly_corrected_gross():
    # current_gross 30000, base 30000 -> swap base for 160h worked at 200/h = 32000.
    assert _hourly_corrected_gross(30000.0, 30000.0, 150.0, 10.0, 200.0) == 32000.0
    # No hours worked or absent -> the monthly base is fully removed.
    assert _hourly_corrected_gross(30000.0, 30000.0, 0.0, 0.0, 200.0) == 0.0


def test_apply_absence_info_to_totals():
    totals = {"brutto_pay": 30000.0}
    absence_info = {
        "total_deduction": 2000.0,
        "total_hours": 16.0,
        "sick_days": 2,
        "sick_hours": 16.0,
        "sick_ob_pay": 300.0,
        "vab_days": 0,
        "vab_hours": 0.0,
        "leave_days": 0,
        "leave_hours": 0.0,
        "off_days": 0,
        "off_hours": 0.0,
        "details": [{"date": "x"}],
    }

    details = _apply_absence_info_to_totals(totals, absence_info)

    assert totals["absence_deduction"] == 2000.0
    assert totals["sick_days"] == 2
    # gross = 30000 - 2000 deduction + 300 sick OB compensation
    assert totals["brutto_pay"] == 28300.0
    assert details == [{"date": "x"}]


def test_summary_reflects_a_sick_day(char_session):
    baseline = summarize_month_for_person(YEAR, MONTH, PERSON_ID, session=char_session, wage_user_id=1)

    work_day = next(
        d
        for d in (datetime.date(YEAR, MONTH, day) for day in range(1, 29))
        if (sh := determine_shift_for_date(d, PERSON_ID)[0]) and sh.code != "OFF"
    )
    char_session.add(Absence(user_id=1, date=work_day, absence_type=AbsenceType.SICK))
    char_session.commit()

    s = summarize_month_for_person(YEAR, MONTH, PERSON_ID, session=char_session, wage_user_id=1)

    assert s["sick_days"] == 1
    assert s["absence_deduction"] > 0
    assert s["brutto_pay"] < baseline["brutto_pay"]


# --- Overtime hour accounting (issue: OT hours counted twice) -------------------
#
# 2026-03-09 is an OFF day for person 1 and 2026-03-25 an N1 day (06:00-14:30),
# so the two OT variants can be pinned on days with no OB and no on-call.
OT_FREE_DAY = datetime.date(2026, 3, 9)
OT_WORK_DAY = datetime.date(2026, 3, 25)
HOURLY_RATE = 200


def _add_ot(session, date, start, end, hours, is_extension):
    session.add(
        OvertimeShift(
            user_id=1,
            date=date,
            start_time=datetime.time(*start),
            end_time=datetime.time(*end),
            hours=hours,
            ot_pay=0.0,
            is_extension=is_extension,
        )
    )
    session.commit()
    clear_schedule_cache()


def _make_hourly(session):
    user = session.query(User).filter(User.id == 1).first()
    user.wage_type = WageType.HOURLY
    user.wage = HOURLY_RATE
    session.commit()
    clear_schedule_cache()


def _summary(session):
    return summarize_month_for_person(YEAR, MONTH, PERSON_ID, session=session, wage_user_id=1)


def test_monthly_baseline_has_no_overtime(char_session):
    s = _summary(char_session)
    assert s["total_hours"] == 144.5
    assert s["ot_hours"] == 0.0
    assert s["brutto_pay"] == 44207.0


def test_monthly_standalone_ot_day_counts_hours_once(char_session):
    baseline = _summary(char_session)
    _add_ot(char_session, OT_FREE_DAY, (8, 0), (16, 0), 8.0, is_extension=False)

    s = _summary(char_session)

    # A standalone (non-extension) OT shift replaces the day's shift with OT,
    # so its 8 hours are worked hours, counted exactly once.
    assert s["ot_hours"] == 8.0
    assert s["total_hours"] == baseline["total_hours"] + 8.0
    # Monthly pay: the monthly base is untouched, OT is paid at wage/72.
    assert s["brutto_pay"] == pytest.approx(baseline["brutto_pay"] + 8.0 * WAGE / 72)


def test_monthly_extension_ot_counts_shift_and_ot(char_session):
    baseline = _summary(char_session)
    _add_ot(char_session, OT_WORK_DAY, (14, 30), (16, 30), 2.0, is_extension=True)

    s = _summary(char_session)

    # An extension keeps the 8.5h shift and adds 2h on top.
    assert s["ot_hours"] == 2.0
    assert s["total_hours"] == baseline["total_hours"] + 2.0
    assert s["brutto_pay"] == pytest.approx(baseline["brutto_pay"] + 2.0 * WAGE / 72)


def test_hourly_baseline_pays_scheduled_hours(char_session):
    _make_hourly(char_session)
    s = _summary(char_session)

    assert s["total_hours"] == 144.5
    # Gross = worked hours x hourly rate, plus OB and on-call on top.
    ob_total = sum(s["ob_pay"].values())
    assert s["brutto_pay"] == pytest.approx(144.5 * HOURLY_RATE + ob_total + s["oncall_pay"])


def test_hourly_standalone_ot_day_is_paid_once(char_session):
    _make_hourly(char_session)
    baseline = _summary(char_session)
    _add_ot(char_session, OT_FREE_DAY, (8, 0), (16, 0), 8.0, is_extension=False)

    s = _summary(char_session)

    # The 8 OT hours are paid through ot_pay only; they must not also be billed
    # as ordinary worked hours.
    assert s["ot_hours"] == 8.0
    assert s["ot_pay"] == pytest.approx(8.0 * HOURLY_RATE)
    assert s["total_hours"] == baseline["total_hours"] + 8.0
    assert s["brutto_pay"] == pytest.approx(baseline["brutto_pay"] + 8.0 * HOURLY_RATE)


def test_hourly_extension_ot_pays_shift_plus_ot(char_session):
    _make_hourly(char_session)
    baseline = _summary(char_session)
    _add_ot(char_session, OT_WORK_DAY, (14, 30), (16, 30), 2.0, is_extension=True)

    s = _summary(char_session)

    assert s["ot_hours"] == 2.0
    assert s["total_hours"] == baseline["total_hours"] + 2.0
    assert s["brutto_pay"] == pytest.approx(baseline["brutto_pay"] + 2.0 * HOURLY_RATE)
