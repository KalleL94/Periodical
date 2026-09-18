"""A part-day absence must render as its own type, with the shortened window.

The day dict already carries both: `start`/`end` are truncated to the worked
portion and `partial_absence` knows its own type. The views used to read the
shift type's nominal times instead, and month hardcoded a sick badge.
"""

import datetime

import pytest
from sqlalchemy.orm import sessionmaker

import app.database.database as db_module
from app.auth.auth import create_access_token
from app.core.schedule import clear_schedule_cache
from app.database.database import (
    Absence,
    AbsenceType,
    PersonHistory,
    RotationEra,
    User,
    UserRole,
    WageType,
)
from tests.conftest import _ROTATION_ERA_PATTERN

# Person 1 works N2 (14:00-22:30) on this date under conftest's rotation pattern.
DAY = datetime.date(2026, 3, 2)
ISO_YEAR, ISO_WEEK, _ = DAY.isocalendar()


@pytest.fixture()
def env(test_db, test_client, monkeypatch):
    monkeypatch.setattr(db_module, "SessionLocal", sessionmaker(bind=test_db.get_bind()))
    clear_schedule_cache()
    test_db.query(RotationEra).delete()
    test_db.add(
        RotationEra(
            start_date=datetime.date(2026, 1, 2),
            end_date=None,
            rotation_length=10,
            weeks_pattern=_ROTATION_ERA_PATTERN,
        )
    )
    test_db.add(
        User(
            id=1,
            username="u1",
            password_hash="x",
            name="User 1",
            role=UserRole.USER,
            wage=30000,
            wage_type=WageType.MONTHLY,
            vacation={},
            must_change_password=0,
            is_active=1,
            person_id=1,
        )
    )
    # The range view builds its days from person-history segments, unlike month
    # and week, so without this row it renders an empty page and proves nothing.
    test_db.add(
        PersonHistory(
            user_id=1,
            person_id=1,
            name="User 1",
            username="u1",
            is_active=1,
            effective_from=datetime.date(2026, 1, 2),
            effective_to=None,
            created_by=1,
        )
    )
    test_db.commit()
    test_client.cookies.set("access_token", create_access_token(data={"sub": "1"}))
    yield test_client, test_db
    clear_schedule_cache()


def _add_absence(session, absence_type, left_at=None, arrived_at=None):
    session.add(
        Absence(
            user_id=1,
            date=DAY,
            absence_type=absence_type,
            left_at=left_at,
            arrived_at=arrived_at,
        )
    )
    session.commit()
    clear_schedule_cache()


VIEWS = {
    "month": f"/month/1?year={DAY.year}&month={DAY.month}",
    "week": f"/week/1?year={ISO_YEAR}&week={ISO_WEEK}",
    "range": "/range/1?from=2026-03-01&to=2026-03-07",
}


def _cell(html: str) -> str:
    """Just the target day's block, from its own day link up to the next one.

    A fixed-width slice is wrong in both directions: too narrow never reaches the
    time element, too wide spills into the next day, which renders the full shift
    window legitimately.
    """
    anchor = f'/day/1/{DAY.year}/{DAY.month}/{DAY.day}"'
    start = html.index(anchor)
    nxt = html.find("/day/1/", start + len(anchor))
    return html[start : nxt if nxt != -1 else len(html)]


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_a_paid_leave_part_day_is_not_labelled_sick(env, view):
    client, session = env
    _add_absence(session, AbsenceType.OFF, left_at="18:00")

    cell = _cell(client.get(VIEWS[view]).text)
    assert "SJ<" not in cell, f"{view} shows the sick marker for a paid-leave day"
    assert "Sjuk" not in cell, f"{view} says Sjuk for a paid-leave day"


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_leaving_early_shortens_the_rendered_window(env, view):
    """The shift runs to 22:30, the person left at 18:00. 22:30 must not show."""
    client, session = env
    _add_absence(session, AbsenceType.OFF, left_at="18:00")

    cell = _cell(client.get(VIEWS[view]).text)
    assert "18:00" in cell, f"{view} does not show the actual end time"
    assert "14:00 - 22:30" not in cell, f"{view} still shows the full shift window"


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_arriving_late_shortens_the_rendered_window(env, view):
    """Late arrival is the mirror case and was never rendered at all."""
    client, session = env
    _add_absence(session, AbsenceType.OFF, arrived_at="17:00")

    cell = _cell(client.get(VIEWS[view]).text)
    assert "17:00" in cell, f"{view} does not show the actual start time"
    assert "14:00 - 22:30" not in cell, f"{view} still shows the full shift window"


def test_the_month_marker_names_the_absence_type(env):
    client, session = env
    _add_absence(session, AbsenceType.OFF, left_at="18:00")

    cell = _cell(client.get(VIEWS["month"]).text)
    assert "OFF" in cell


def test_a_sick_part_day_still_reads_as_sick(env):
    """The original behaviour has to survive the fix."""
    client, session = env
    _add_absence(session, AbsenceType.SICK, left_at="18:00")

    cell = _cell(client.get(VIEWS["month"]).text)
    assert "SICK" in cell or "Sjuk" in cell


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_every_view_says_why_the_day_is_short(env, view):
    """A truncated window with no marker reads as a shorter scheduled shift."""
    client, session = env
    _add_absence(session, AbsenceType.OFF, left_at="18:00")

    cell = _cell(client.get(VIEWS[view]).text)
    assert "OFF" in cell, f"{view} shows no marker for the part-day absence"


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_a_late_arrival_is_named_as_such(env, view):
    """LATE_PAID exists so this does not read as a whole day off."""
    client, session = env
    _add_absence(session, AbsenceType.LATE_PAID, arrived_at="17:00")

    cell = _cell(client.get(VIEWS[view]).text)
    assert "LATE_PAID" in cell or "Late" in cell or "Sen" in cell
