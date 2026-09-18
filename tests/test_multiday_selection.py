"""Selecting days in the month and week calendars.

Both views render identical cell markup, so one script and one CSS block serve
both. These tests pin the attributes that script depends on.
"""

import datetime
from pathlib import Path

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


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_the_drawer_renders_hidden_with_the_edit_forms(env, view):
    client, _ = env
    html = client.get(VIEWS[view]).text
    assert 'id="day-edit-drawer"' in html
    for action in ("/overtime/add", "/absence/add", "/oncall/add", "/shift-override/add"):
        assert f'action="{action}"' in html


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_every_drawer_form_returns_to_this_view(env, view):
    """Without return_to a multi-day edit lands on some day page instead."""
    import re

    client, _ = env
    html = client.get(VIEWS[view]).text
    drawer = html[html.index('id="day-edit-drawer"') :]
    forms = re.findall(r'<form[^>]*action="/(?:overtime|absence|oncall|shift-override)/add".*?</form>', drawer, re.S)
    assert len(forms) == 4, f"expected four add forms, found {len(forms)}"
    for form in forms:
        assert 'name="return_to"' in form


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_the_drawer_shows_no_single_day_state(env, view):
    """With many days selected there is no one day's absence or overtime to show."""
    client, _ = env
    html = client.get(VIEWS[view]).text
    drawer = html[html.index('id="day-edit-drawer"') :]
    assert "/overtime/add" in drawer
    assert "/delete" not in drawer


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_the_drawer_gets_the_panel_behaviour(env, view):
    """The tabs, the hours calculator and the ETC toggle are the partial's own
    behaviour. Left in day.html they are dead everywhere else, so every panel
    renders at once in the drawer."""
    client, _ = env
    html = client.get(VIEWS[view]).text
    assert "day-edit-panel.js" in html


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_the_drawer_gets_the_panel_styles(env, view):
    """Without .day-tab-panel { display: none } every tab shows at once."""
    client, _ = env
    html = client.get(VIEWS[view]).text
    assert "components.css" in html
    css = (Path("app/static/css/components.css")).read_text()
    assert ".day-tab-panel {" in css
    assert ".day-tab-panel.is-active {" in css


def test_the_panel_behaviour_is_not_inline_in_the_day_page():
    """A copy left in day.html would drift out of step with the shared one."""
    day = Path("app/templates/day.html").read_text()
    assert "querySelectorAll('.day-tab')" not in day
    assert ".day-tab-panel {" not in day


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_the_drawer_offers_clearing(env, view):
    """Without this the only way to undo a multi-day edit is one day page at a time."""
    client, _ = env
    html = client.get(VIEWS[view]).text
    drawer = html[html.index('id="day-edit-drawer"') :]
    assert 'action="/day-edit/clear"' in drawer
    for target in ("extra:before", "absence", "oncall", "shift"):
        assert f'value="{target}"' in drawer


def test_the_day_page_does_not_offer_the_bulk_clear(env):
    """The day page has per-row delete buttons, which say exactly what they remove."""
    client, _ = env
    html = client.get(f"/day/1/{DAY.year}/{DAY.month}/{DAY.day}").text
    assert 'action="/day-edit/clear"' not in html


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_the_drawer_offers_a_per_day_tab(env, view):
    client, _ = env
    html = client.get(VIEWS[view]).text
    drawer = html[html.index('id="day-edit-drawer"') :]
    assert 'id="tab-perdag"' in drawer
    assert 'id="per-day-row"' in drawer
    assert 'action="/day-edit/bulk"' in drawer


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_every_per_day_cell_is_labelled(env, view):
    """tables.css turns cells into cards below 800px using data-label; a cell
    without one renders as an unlabelled field on a phone."""
    import re

    client, _ = env
    html = client.get(VIEWS[view]).text
    template = re.search(r'<template id="per-day-row">.*?</template>', html, re.S).group(0)
    cells = re.findall(r"<td[^>]*>", template)
    assert cells
    assert all("data-label=" in cell for cell in cells)


def test_the_day_page_has_no_per_day_tab(env):
    """One day does not need a per-day table."""
    client, _ = env
    html = client.get(f"/day/1/{DAY.year}/{DAY.month}/{DAY.day}").text
    assert 'id="tab-perdag"' not in html


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_the_per_day_table_does_not_opt_out_of_the_card_layout(env, view):
    """keep-table keeps a table a table below 800px. Twelve controls per row in a
    table is exactly what the per-day design exists to avoid."""
    import re

    client, _ = env
    html = client.get(VIEWS[view]).text
    table = re.search(r'<table[^>]*id="per-day-table"[^>]*>', html).group(0)
    assert "keep-table" not in table
