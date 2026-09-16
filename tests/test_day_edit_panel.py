"""The edit panel renders the same forms after the move into a partial."""

import datetime
import re

import pytest

import app.database.database as db_module
from app.auth.auth import create_access_token
from app.core.schedule import clear_schedule_cache
from app.database.database import (
    RotationEra,
    User,
    UserRole,
    WageType,
)
from tests.conftest import _ROTATION_ERA_PATTERN

DAY = datetime.date(2026, 3, 2)


@pytest.fixture()
def env(test_db, test_client, monkeypatch):
    monkeypatch.setattr(db_module, "SessionLocal", lambda: test_db)
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
    test_db.commit()
    test_client.cookies.set("access_token", create_access_token(data={"sub": "1"}))
    yield test_client, test_db
    clear_schedule_cache()


def _page(client):
    resp = client.get(f"/day/1/{DAY.year}/{DAY.month}/{DAY.day}")
    assert resp.status_code == 200
    return resp.text


def test_the_day_page_still_offers_every_edit_form(env):
    client, _ = env
    html = _page(client)
    for action in ("/overtime/add", "/absence/add", "/oncall/add", "/shift-override/add"):
        assert f'action="{action}"' in html


def test_every_post_form_carries_a_csrf_token(env):
    """A POST form without it returns 403, so a missing one is an invisible break."""
    client, _ = env
    html = _page(client)
    forms = re.findall(r'<form[^>]*method="POST".*?</form>', html, re.S)
    assert forms
    missing = [f[:120] for f in forms if "csrf_token" not in f]
    assert not missing, missing


def test_the_four_add_forms_post_dates_not_date(env):
    client, _ = env
    html = _page(client)
    assert 'name="dates"' in html
    for form in re.findall(r'<form[^>]*action="/(?:overtime|absence|oncall|shift-override)/add".*?</form>', html, re.S):
        assert 'name="date"' not in form


def test_the_tid_tab_lists_every_overtime_row(env):
    """Two rows on one day must both appear, each with its own delete button."""
    from app.database.database import OvertimeShift

    client, session = env
    for side, start, end in (("before", "05:00", "06:00"), ("after", "14:30", "16:30")):
        session.add(
            OvertimeShift(
                user_id=1,
                date=DAY,
                start_time=datetime.time.fromisoformat(start),
                end_time=datetime.time.fromisoformat(end),
                hours=1.0,
                ot_pay=0.0,
                kind="ot",
                side=side,
            )
        )
    session.commit()
    clear_schedule_cache()

    html = _page(client)
    rows = session.query(OvertimeShift).all()
    assert len(rows) == 2
    for row in rows:
        assert f"/overtime/{row.id}/delete" in html


def test_the_add_form_offers_kind_and_side(env):
    client, _ = env
    html = _page(client)
    assert 'name="kind"' in html
    assert 'name="side"' in html


def test_all_four_tabs_render(env):
    client, _ = env
    html = _page(client)
    for tab in ("tab-tid", "tab-franvaro", "tab-beredskap", "tab-byte"):
        assert f'id="{tab}"' in html
