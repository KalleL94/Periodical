"""Substitute-to-user account link (issue #290): helper lookups, pay
integration for pre-employment substitute shifts, and double-count guards."""

import datetime

import pytest
from sqlalchemy.orm import sessionmaker

import app.database.database as db_module
from app.core.schedule import clear_schedule_cache
from app.core.schedule.period import get_linked_substitutes_for_user
from app.database.database import (
    RotationEra,
    Substitute,
    User,
    UserRole,
    WageType,
)
from tests.conftest import _ROTATION_ERA_PATTERN


@pytest.fixture()
def env(test_db, test_client, monkeypatch):
    """Bind the global SessionLocal to the test DB and seed the rotation era
    (same technique as tests/test_day_view_consistency.py)."""
    engine = test_db.get_bind()
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(db_module, "SessionLocal", session_local)
    clear_schedule_cache()
    test_db.add(
        RotationEra(
            start_date=datetime.date(2026, 1, 2),
            end_date=None,
            rotation_length=10,
            weeks_pattern=_ROTATION_ERA_PATTERN,
        )
    )
    test_db.commit()
    yield test_client, test_db
    clear_schedule_cache()


def _make_user(session, uid, person_id, wage=30000, wage_type=WageType.MONTHLY):
    user = User(
        id=uid,
        username=f"u{uid}",
        password_hash="x",
        name=f"User {uid}",
        role=UserRole.USER,
        wage=wage,
        wage_type=wage_type,
        vacation={},
        must_change_password=0,
        is_active=1,
        person_id=person_id,
    )
    session.add(user)
    session.commit()
    return user


def test_get_linked_substitutes_for_user(env):
    _, session = env
    _make_user(session, 1, 1)
    _make_user(session, 2, 2)
    linked_active = Substitute(name="Sub A", is_active=1, user_id=1, hourly_wage=180)
    linked_archived = Substitute(name="Sub B", is_active=0, user_id=1, hourly_wage=170)
    unlinked = Substitute(name="Sub C", is_active=1)
    other_user = Substitute(name="Sub D", is_active=1, user_id=2)
    session.add_all([linked_active, linked_archived, unlinked, other_user])
    session.commit()

    result = get_linked_substitutes_for_user(session, 1)
    names = sorted(s.name for s in result)
    # Archived substitutes stay included: their history belongs to the user.
    assert names == ["Sub A", "Sub B"]

    assert get_linked_substitutes_for_user(session, 3) == []
    assert get_linked_substitutes_for_user(session, None) == []
    assert get_linked_substitutes_for_user(None, 1) == []
