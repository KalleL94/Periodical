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
from app.core.schedule.summary import summarize_month_for_person
from app.database.database import Absence, AbsenceType, Base, RotationEra, User, UserRole, WageType

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
