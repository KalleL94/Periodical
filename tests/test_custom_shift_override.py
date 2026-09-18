"""A shift override with code ETC renders a custom block with its own times."""

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
    Base,
    RotationEra,
    ShiftOverride,
    User,
    UserRole,
    WageType,
)

TEST_DB_URL = "sqlite:///file:test_custom_override_memdb?mode=memory&cache=shared&uri=true"
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

TARGET = datetime.date(2026, 3, 2)


@pytest.fixture
def ovr_session(monkeypatch):
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
            username="ovruser",
            password_hash="x",
            name="Override",
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


@pytest.fixture
def day_for_override(ovr_session):
    def build(shift_code, start=None, end=None, label=None):
        ovr_session.add(
            ShiftOverride(
                user_id=1,
                date=TARGET,
                shift_code=shift_code,
                start_time=datetime.time.fromisoformat(start) if start else None,
                end_time=datetime.time.fromisoformat(end) if end else None,
                label=label,
            )
        )
        ovr_session.commit()
        clear_schedule_cache()
        days = generate_month_data(TARGET.year, TARGET.month, 1, session=ovr_session)
        return next(d for d in days if d["date"] == TARGET)

    return build


def test_etc_override_uses_its_own_times_and_label(day_for_override):
    day = day_for_override(shift_code="ETC", start="09:00", end="12:00", label="Kurs")
    assert day["shift"].code == "ETC"
    assert day["shift"].label == "Kurs"
    assert day["hours"] == 3.0


def test_etc_override_earns_ob_on_its_own_interval(day_for_override):
    """An evening ETC block earns OB the way a normal shift would."""
    day = day_for_override(shift_code="ETC", start="18:00", end="22:00", label="Moete")
    assert sum(day["ob"].values()) > 0


def test_etc_crossing_midnight_gets_the_hours_right(day_for_override):
    day = day_for_override(shift_code="ETC", start="22:00", end="02:00", label="Natt")
    assert day["hours"] == 4.0


def test_etc_without_times_falls_back_rather_than_crashing(day_for_override):
    """_synthetic_shift returns None without both times, so the code lookup runs
    and finds nothing. The day must resolve to no shift, not raise."""
    day = day_for_override(shift_code="ETC")
    assert day["hours"] == 0.0


def test_a_plain_n2_override_is_unaffected(day_for_override):
    day = day_for_override(shift_code="N2")
    assert day["shift"].code == "N2"
    assert day["hours"] == 8.5


def test_a_normal_shift_can_be_given_its_own_times(day_for_override):
    """You worked N1 but stayed until 16:30. It is still N1, just a longer window."""
    day = day_for_override(shift_code="N1", start="06:00", end="16:30")
    assert day["shift"].code == "N1"
    assert day["hours"] == 10.5
    assert day["start"].strftime("%H:%M") == "06:00"
    assert day["end"].strftime("%H:%M") == "16:30"


def test_a_retimed_shift_keeps_its_name_and_colour(day_for_override):
    """Only the window moves. The badge must still read as the rotation shift."""
    from app.core.schedule.core import get_shift_types

    real = next(s for s in get_shift_types() if s.code == "N1")
    day = day_for_override(shift_code="N1", start="06:00", end="16:30")
    assert day["shift"].label == real.label
    assert day["shift"].color == real.color


def test_a_retimed_shift_earns_ob_on_the_new_window(day_for_override):
    """An evening stretch earns OB the shift would not have earned on its own."""
    plain = day_for_override(shift_code="N1")
    ob_plain = sum(plain["ob"].values())

    day = day_for_override(shift_code="N1", start="06:00", end="20:00")
    assert sum(day["ob"].values()) > ob_plain


def test_an_override_without_times_still_uses_the_shift_type(day_for_override):
    day = day_for_override(shift_code="N1")
    assert day["shift"].code == "N1"
    assert day["hours"] == 8.5


def test_starting_later_shortens_the_day(day_for_override):
    """The mirror case: a colleague takes the start of your shift."""
    day = day_for_override(shift_code="N2", start="16:00", end="22:30")
    assert day["hours"] == 6.5
