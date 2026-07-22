"""Characterization tests for the dashboard week summary.

The dashboard handler used to recompute OT, on-call and OB itself instead of
summing the day dicts build_week_data already returns, which made it a fourth
place the pay rules had to be kept in sync. These tests pin what the dashboard
reports for a fixture week, and assert that the figures agree with the
canonical week data.

Same technique as tests/test_api_v1_characterization.py: bind the global
SessionLocal to the test DB and seed a rotation era so schedule internals and
the HTTP route share one database. The template context is captured by
monkeypatching the route's render(), so the numbers are asserted directly
rather than scraped out of HTML.
"""

import datetime

import pytest
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import sessionmaker

import app.database.database as db_module
import app.routes.dashboard as dashboard_module
from app.auth.auth import create_access_token
from app.core.schedule import _cached_special_rules, clear_schedule_cache, generate_period_data, ob_rules
from app.core.schedule.ob import compute_day_ob_pay
from app.core.schedule.summary import day_worked_hours
from app.database.database import (
    Absence,
    AbsenceType,
    OnCallOverride,
    OnCallOverrideType,
    OvertimeShift,
    RotationEra,
    User,
    UserRole,
    WageType,
)
from tests.conftest import _ROTATION_ERA_PATTERN

# ISO week 12 of 2026 for rotation position 1: OFF, OC, N3, N3, N3, N3, OFF.
WEEK_YEAR = 2026
WEEK_NO = 12
MONDAY = datetime.date(2026, 3, 16)
OC_DAY = datetime.date(2026, 3, 17)
N3_DAY = datetime.date(2026, 3, 18)
OT_DAY = MONDAY  # OT placed on the OFF Monday
WAGE = 30000

SUMMARY_KEYS = ("total_hours", "ob_hours", "total_pay", "oc_pay", "ot_pay", "absence_deduction")


@pytest.fixture()
def dash_env(test_db, test_client, monkeypatch):
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
    test_db.add(
        User(
            id=1,
            username="dashuser",
            password_hash="x",
            name="Dash User",
            role=UserRole.USER,
            wage=WAGE,
            wage_type=WageType.MONTHLY,
            person_id=1,
            tax_table="33",
            vacation={},
            must_change_password=0,
            is_active=1,
        )
    )
    test_db.add(
        OvertimeShift(
            user_id=1,
            date=OT_DAY,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(16, 0),
            hours=8.0,
            ot_pay=0.0,
            is_extension=False,
        )
    )
    test_db.commit()

    test_client.cookies.set("access_token", f"Bearer {create_access_token(data={'sub': '1'})}")

    captured = {}

    def _capture(template_name, context, status_code=200, headers=None):
        captured["template"] = template_name
        captured["context"] = context
        return HTMLResponse("", status_code=status_code)

    monkeypatch.setattr(dashboard_module, "render", _capture)

    yield test_client, test_db, captured
    clear_schedule_cache()


def _week_summary(client, captured) -> dict:
    resp = client.get(f"/?week={WEEK_NO}&wyear={WEEK_YEAR}")
    assert resp.status_code == 200, resp.text
    assert captured["template"] == "dashboard.html"
    return captured["context"]["week_summary"]


def _rounded(summary: dict) -> dict:
    return {k: round(summary[k], 2) for k in SUMMARY_KEYS}


def _canonical_week_summary(session) -> dict:
    """What the canonical period path says about the same week.

    Applies the same aggregation the month/year summary applies
    (_process_day_for_summary): OB through compute_day_ob_pay, on-call hours
    excluded from worked hours, overtime hours added.
    """
    days = generate_period_data(MONDAY, MONDAY + datetime.timedelta(days=6), person_id=1, session=session)
    combined_rules = ob_rules + _cached_special_rules(WEEK_YEAR)
    total_hours = 0.0
    ob_hours = 0.0
    ob_pay = 0.0
    for day in days:
        hours, pay, _ = compute_day_ob_pay(day, combined_rules, WAGE)
        ob_hours += sum(hours.values())
        ob_pay += sum(pay.values())
        total_hours += day_worked_hours(day)
    return {
        "total_hours": round(total_hours, 2),
        "ob_hours": round(ob_hours, 2),
        "total_pay": round(ob_pay, 2),
        "oc_pay": round(sum(d.get("oncall_pay", 0.0) for d in days), 2),
        "ot_pay": round(sum(d.get("ot_pay", 0.0) for d in days), 2),
    }


def test_week_summary_context_keys(dash_env):
    client, _, captured = dash_env
    summary = _week_summary(client, captured)
    assert set(summary) == set(SUMMARY_KEYS)


def test_week_summary_figures(dash_env):
    """Pinned figures for the fixture week: 4 x N3 plus an 8h OT day, one on-call day.

    Before the dashboard was moved onto the canonical day dicts it reported
    total_hours 50.5, ob_hours 38.5 and total_pay 2837.50 for this same week: it
    priced OB on the overtime day (which carries no OB anywhere else) and counted
    the OT day's hours from the OT shift type's nominal length.

    total_hours is 4 x 8.5h N3 plus the 8h OT day = 42.0. It read 50.0 until the
    OT hours stopped being counted twice (once as the day's hours, once as OT).
    """
    client, _, captured = dash_env
    assert _rounded(_week_summary(client, captured)) == {
        "total_hours": 42.0,
        "ob_hours": 34.0,
        "total_pay": 2612.5,
        "oc_pay": 1800.0,
        "ot_pay": 3333.33,
        "absence_deduction": 0.0,
    }


def test_week_summary_matches_canonical_week_data(dash_env):
    """The dashboard must not disagree with the week view it summarizes."""
    client, session, captured = dash_env
    summary = _rounded(_week_summary(client, captured))
    canonical = _canonical_week_summary(session)
    for key, value in canonical.items():
        assert summary[key] == pytest.approx(value, abs=0.01), key


def test_week_summary_matches_canonical_with_oncall_override(dash_env):
    """An added on-call day and a removed one must move the dashboard's on-call pay."""
    client, session, captured = dash_env
    session.add(OnCallOverride(user_id=1, date=MONDAY, override_type=OnCallOverrideType.ADD))
    session.add(OnCallOverride(user_id=1, date=OC_DAY, override_type=OnCallOverrideType.REMOVE))
    session.commit()
    clear_schedule_cache()

    summary = _rounded(_week_summary(client, captured))
    canonical = _canonical_week_summary(session)
    for key, value in canonical.items():
        assert summary[key] == pytest.approx(value, abs=0.01), key


def test_week_summary_overtime_on_oncall_day(dash_env):
    """Overtime worked during an on-call day reduces that day's on-call pay."""
    client, session, captured = dash_env
    session.add(
        OvertimeShift(
            user_id=1,
            date=OC_DAY,
            start_time=datetime.time(22, 0),
            end_time=datetime.time(6, 0),  # crosses midnight
            hours=8.0,
            ot_pay=0.0,
            is_extension=False,
        )
    )
    session.commit()
    clear_schedule_cache()

    summary = _rounded(_week_summary(client, captured))
    canonical = _canonical_week_summary(session)
    assert summary["oc_pay"] < 1800.0
    for key, value in canonical.items():
        assert summary[key] == pytest.approx(value, abs=0.01), key


def test_week_summary_absence_day(dash_env):
    """A full sick day drops the shift's hours/OB and reports a wage deduction."""
    client, session, captured = dash_env
    session.add(Absence(user_id=1, date=N3_DAY, absence_type=AbsenceType.SICK))
    session.commit()
    clear_schedule_cache()

    summary = _rounded(_week_summary(client, captured))
    canonical = _canonical_week_summary(session)
    assert summary["absence_deduction"] > 0
    for key, value in canonical.items():
        assert summary[key] == pytest.approx(value, abs=0.01), key
