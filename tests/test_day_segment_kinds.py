"""How a day's overtime and extra-time rows reach the day dict.

Person 1 works N2 (14:00-22:30, 8.5 h) on 2026-03-02 under the era below.
"""

import datetime
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

# ruff: noqa: E402
import app.database.database as db_module
from app.core.schedule import clear_schedule_cache
from app.core.schedule.period import generate_month_data
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

TEST_DB_URL = "sqlite:///file:test_segment_kinds_memdb?mode=memory&cache=shared&uri=true"
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

# An N2 day (14:00-22:30, 8.5 h) for person 1 under ERA_PATTERN, which is why every
# expected number below is 8.5-based. test_the_baseline_day_is_n2 is the guard.
TARGET = datetime.date(2026, 3, 2)


@pytest.fixture
def seg_session(monkeypatch):
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
            username="seguser",
            password_hash="x",
            name="Segments",
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


def _day(session, date=TARGET):
    clear_schedule_cache()
    days = generate_month_data(date.year, date.month, 1, session=session)
    return next(d for d in days if d["date"] == date)


def _add(session, **kw):
    """Insert one overtime_shifts row on TARGET for person 1."""
    session.add(
        OvertimeShift(
            user_id=1,
            date=kw.get("date", TARGET),
            start_time=datetime.time.fromisoformat(kw["start"]),
            end_time=datetime.time.fromisoformat(kw["end"]),
            hours=kw["hours"],
            ot_pay=0.0,
            kind=kw["kind"],
            side=kw["side"],
        )
    )
    session.commit()


@pytest.fixture
def day_for(seg_session):
    def build(**kw):
        _add(seg_session, **kw)
        return _day(seg_session)

    return build


@pytest.fixture
def day_for_many(seg_session):
    def build(rows):
        for row in rows:
            _add(seg_session, **row)
        return _day(seg_session)

    return build


@pytest.fixture
def day_for_vacation(seg_session):
    def build(**kw):
        seg_session.add(Absence(user_id=1, date=TARGET, absence_type=AbsenceType.VACATION))
        seg_session.commit()
        _add(seg_session, **kw)
        return _day(seg_session)

    return build


def test_the_baseline_day_is_n2(seg_session):
    """Guards every expected number below. If this fails, TARGET moved."""
    day = _day(seg_session)
    assert day["shift"].code == "N2"
    assert day["hours"] == 8.5


def test_extra_time_adds_hours_and_ob_but_no_ot_pay(day_for):
    """One hour of extra time before an N2 shift: 9.5 worked hours, no OT pay."""
    day = day_for(kind="extra", side="before", start="13:00", end="14:00", hours=1.0)
    assert day["hours"] == 9.5
    assert day["ot_pay"] == 0.0
    assert day["ot_hours"] == 0.0


def test_overtime_after_keeps_hours_out_of_the_segment_list(day_for):
    """The 8.5 h shift stays 8.5 in day["hours"]; the 2 h live in ot_hours alone.

    day_worked_hours adds the two, so a row counted in both would report 12.5.
    """
    from app.core.schedule.summary import day_worked_hours

    day = day_for(kind="ot", side="after", start="22:30", end="00:30", hours=2.0)
    assert day["hours"] == 8.5
    assert day["ot_hours"] == 2.0
    assert day_worked_hours(day) == 10.5


def test_overtime_before_and_after_on_the_same_day_both_pay(day_for_many):
    day = day_for_many(
        [
            dict(kind="ot", side="before", start="05:00", end="06:00", hours=1.0),
            dict(kind="ot", side="after", start="22:30", end="00:30", hours=2.0),
        ]
    )
    assert day["ot_hours"] == 3.0
    assert day["ot_pay"] > 0


def test_ot_details_reports_the_after_row(day_for_many):
    """summary.py splits OT across midnight from ot_details, and only the
    after row can cross midnight."""
    day = day_for_many(
        [
            dict(kind="ot", side="before", start="05:00", end="06:00", hours=1.0),
            dict(kind="ot", side="after", start="22:30", end="00:30", hours=2.0),
        ]
    )
    assert day["ot_details"]["start_time"].startswith("22:30")
    assert day["ot_details"]["is_extension"] is True


def test_called_in_overtime_still_replaces_the_shift(day_for):
    day = day_for(kind="ot", side="full", start="14:00", end="22:30", hours=8.5)
    assert day["shift"].code == "OT"
    assert day["ot_details"]["is_extension"] is False


def test_a_vacation_day_still_suppresses_overtime_pay(day_for_vacation):
    """Issue #285: the vacation guard outranks every overtime row."""
    day = day_for_vacation(kind="ot", side="after", start="22:30", end="00:30", hours=2.0)
    assert day["ot_pay"] == 0.0
