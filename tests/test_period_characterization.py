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
from app.core.schedule.period import generate_month_data
from app.database.database import Absence, AbsenceType, Base, RotationEra, User, UserRole, WageType

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
    # Week-based parental leave (User.parental_leave JSON) renders LEAVE for the whole ISO week.
    user = char_session.query(User).filter(User.id == 1).first()
    user.parental_leave = {"2026": [11]}  # ISO week 11 = 9-15 March 2026
    char_session.commit()

    days = generate_month_data(2026, 3, 1, session=char_session)
    leave_days = [d for d in days if d["shift"] and d["shift"].code == "LEAVE"]

    assert len(leave_days) == 7
    assert all(d["hours"] == 0.0 for d in leave_days)
