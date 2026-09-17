"""Selecting days in the month and week calendars.

Both views render identical cell markup, so one script and one CSS block serve
both. These tests pin the attributes that script depends on.
"""

import datetime

import pytest
from sqlalchemy.orm import sessionmaker

import app.database.database as db_module
from app.auth.auth import create_access_token
from app.core.schedule import clear_schedule_cache
from app.database.database import (
    PersonHistory,
    RotationEra,
    User,
    UserRole,
    WageType,
)
from tests.conftest import _ROTATION_ERA_PATTERN

DAY = datetime.date(2026, 3, 2)
ISO_YEAR, ISO_WEEK, _ = DAY.isocalendar()

VIEWS = {
    "month": f"/month/1?year={DAY.year}&month={DAY.month}",
    "week": f"/week/1?year={ISO_YEAR}&week={ISO_WEEK}",
}


@pytest.fixture()
def env(test_db, test_client, monkeypatch):
    # A real sessionmaker: a bare `lambda: test_db` detaches the User on views
    # that open several sessions.
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


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_every_calendar_cell_carries_its_date(env, view):
    client, _ = env
    html = client.get(VIEWS[view]).text
    assert f'data-date="{DAY.isoformat()}"' in html


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_the_selection_script_is_loaded(env, view):
    client, _ = env
    html = client.get(VIEWS[view]).text
    assert "day-selection.js" in html


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_cells_are_marked_as_toggles_for_screen_readers(env, view):
    client, _ = env
    html = client.get(VIEWS[view]).text
    assert 'aria-pressed="false"' in html
