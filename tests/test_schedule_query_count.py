"""The schedule views must not issue a query per person per day.

Naming each day's holder used to query PersonHistory once or more for every
(position, day) pair, so GET /year ran thousands of queries on one request and
blocked the single worker for seconds. The views now preload person history once
per generate call. These bounds fail if a per-day lookup comes back.
"""

import datetime

import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

import app.database.database as db_module
from app.core.schedule import clear_schedule_cache
from app.core.schedule.person_history import add_person_change, end_employment, start_employment
from app.database.database import RotationEra, User, UserRole, WageType
from tests.conftest import _ROTATION_ERA_PATTERN


def _make_user(session, uid, name):
    session.add(
        User(
            id=uid,
            username=f"u{uid}",
            password_hash="x",
            name=name,
            role=UserRole.USER,
            wage=30000,
            wage_type=WageType.MONTHLY,
            vacation={},
            must_change_password=0,
            is_active=1,
            person_id=uid if uid <= 10 else None,
        )
    )
    session.commit()


@pytest.fixture()
def counted_env(test_db, test_client, monkeypatch):
    """A seeded rotation with history, a vacancy and legacy positions, plus a query counter."""
    engine = test_db.get_bind()
    monkeypatch.setattr(db_module, "SessionLocal", sessionmaker(autocommit=False, autoflush=False, bind=engine))
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
    for uid in range(1, 13):
        _make_user(test_db, uid, f"Person{uid}")
    # Positions 1-4 have history (a mid-year change and a vacancy); 5-10 fall back
    # to the User table.
    for pid in (1, 2, 3, 4):
        start_employment(test_db, pid, pid, f"Person{pid}", f"u{pid}", datetime.date(2026, 1, 2), created_by=1)
    add_person_change(
        test_db,
        old_user_id=3,
        new_user_id=11,
        person_id=3,
        new_name="Person11",
        new_username="u11",
        effective_from=datetime.date(2026, 6, 1),
        created_by=1,
    )
    end_employment(test_db, 4, 4, end_date=datetime.date(2026, 8, 4))

    queries = []
    event.listen(engine, "before_cursor_execute", lambda conn, cur, statement, *rest: queries.append(statement))

    def queries_for(path):
        test_client.get(path)  # warm the per-date rotation era cache
        queries.clear()
        resp = test_client.get(path)
        assert resp.status_code == 200
        return queries

    yield queries_for
    clear_schedule_cache()


def test_year_view_query_count_does_not_scale_with_days(counted_env):
    # 365 days times 10 positions; the old per-day lookups ran several thousand.
    assert len(counted_env("/year?year=2026")) < 365


def test_month_view_person_history_queries_do_not_scale_with_days(counted_env):
    # The month view runs one generate call per holder column, so its person
    # history lookups grow with the columns (about 160 here). They must stay below
    # one per person per day: the old per-day lookups ran over 900.
    queries = counted_env("/month?year=2026&month=8")
    assert sum("FROM person_history" in q for q in queries) < 31 * 10
