"""The /year/<id> and /statistics/<id> pages must show the same money.

Both routes summarise the same year for the same person, fold the vacation
supplement into gross/net, and inject the employment transition payout. They
used to do that with two separate copies of the arithmetic, so the headline
gross/net figures drifted apart. These tests pin them together.

Schedule internals read the rotation era through the global SessionLocal, so
the fixture binds it to the same in-memory engine the routes use.
"""

import datetime

import pytest
from sqlalchemy.orm import sessionmaker

import app.database.database as db_module
import app.routes.schedule_personal as schedule_personal
import app.routes.statistics as statistics
from app.auth.auth import create_access_token
from app.core.schedule import clear_schedule_cache
from app.core.schedule.person_history import start_employment
from app.database.database import (
    ConsultantSalaryType,
    EmploymentTransition,
    RotationEra,
    User,
    UserRole,
    WageType,
)
from tests.conftest import _ROTATION_ERA_PATTERN

YEAR = 2026
USER_ID = 11


@pytest.fixture()
def env(test_db, test_client, monkeypatch):
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
    user = User(
        id=USER_ID,
        username="peter1",
        password_hash="x",
        name="Peter",
        role=UserRole.USER,
        wage=30000,
        wage_type=WageType.MONTHLY,
        person_id=1,
        # Weeks 28 and 29 are vacation, so several months carry SEM days.
        vacation={str(YEAR): [28, 29, 30]},
        employment_start_date=datetime.date(2024, 1, 1),
        vacation_days_per_year=25,
        vacation_year_start_month=4,
        must_change_password=0,
    )
    test_db.add(user)
    test_db.commit()
    start_employment(test_db, USER_ID, 1, "Peter", "peter1", datetime.date(2026, 1, 2), created_by=1)

    token = create_access_token(data={"sub": str(USER_ID)})
    test_client.cookies.set("access_token", f"Bearer {token}")

    yield test_client, test_db

    clear_schedule_cache()


def _add_transition(session):
    session.add(
        EmploymentTransition(
            user_id=USER_ID,
            transition_date=datetime.date(YEAR, 6, 1),
            consultant_salary_type=ConsultantSalaryType.TRAILING,
            consultant_vacation_days=13.0,
            consultant_supplement_pct=0.0043,
        )
    )
    session.commit()


def _capture(monkeypatch, module):
    """Record the template context a route renders, without skipping the render."""
    captured: dict = {}
    real = module.render

    def spy(template_name, context, *args, **kwargs):
        captured.update(context)
        return real(template_name, context, *args, **kwargs)

    monkeypatch.setattr(module, "render", spy)
    return captured


def _both_pages(env, monkeypatch):
    client, _session = env
    year_ctx = _capture(monkeypatch, schedule_personal)
    assert client.get(f"/year/{USER_ID}?year={YEAR}").status_code == 200
    year_ctx = dict(year_ctx)

    stats_ctx = _capture(monkeypatch, statistics)
    assert client.get(f"/statistics/{USER_ID}?year={YEAR}").status_code == 200
    return year_ctx, dict(stats_ctx)


def test_year_totals_match_without_transition(env, monkeypatch):
    year_ctx, stats_ctx = _both_pages(env, monkeypatch)

    assert year_ctx["year_summary"]["total_brutto"] > 0
    assert stats_ctx["year_summary"]["total_brutto"] == year_ctx["year_summary"]["total_brutto"]
    assert stats_ctx["year_summary"]["total_netto"] == year_ctx["year_summary"]["total_netto"]


def test_vacation_supplement_folded_identically(env, monkeypatch):
    year_ctx, stats_ctx = _both_pages(env, monkeypatch)

    year_months = {m["payment_month"]: m for m in year_ctx["months"] if m.get("payment_month")}
    stats_months = {m["payment_month"]: m for m in stats_ctx["months"] if m.get("payment_month")}
    assert any(m.get("vacation_supplement") for m in year_months.values()), "fixture must produce SEM days"

    for pm, ym in year_months.items():
        sm = stats_months[pm]
        assert sm["vacation_supplement"] == ym["vacation_supplement"], f"payment month {pm}"
        assert sm["brutto_pay"] == ym["brutto_pay"], f"payment month {pm}"
        assert sm["netto_pay"] == ym["netto_pay"], f"payment month {pm}"


@pytest.mark.parametrize("wage", [29100, 30000])
def test_year_totals_match_with_employment_transition(env, monkeypatch, wage):
    """The transition payout must not shift the year totals between the two pages.

    29100 is a wage where the two routes' rounding used to disagree: the year view
    rounds the consultant and the direct-employer rows separately, so a route that
    rounds their sum instead lands one krona off.
    """
    _client, session = env
    session.get(User, USER_ID).wage = wage
    session.commit()
    _add_transition(session)

    year_ctx, stats_ctx = _both_pages(env, monkeypatch)

    assert any(m.get("transition_direct") for m in year_ctx["months"]), "transition payout must be injected"
    assert stats_ctx["year_summary"]["total_brutto"] == year_ctx["year_summary"]["total_brutto"]
    assert stats_ctx["year_summary"]["total_netto"] == year_ctx["year_summary"]["total_netto"]

    # Row by row too: the transition month is the row the two pages used to disagree on.
    def _rows(ctx):
        return [(m["payment_date"], m.get("brutto_pay"), m.get("netto_pay")) for m in ctx["months"]]

    assert _rows(stats_ctx) == _rows(year_ctx)


def test_statistics_charts_cover_every_payment_month(env, monkeypatch):
    """The chart series must stay one point per payment month, transition included."""
    _client, session = env
    _add_transition(session)

    _year_ctx, stats_ctx = _both_pages(env, monkeypatch)

    labels = stats_ctx["chart_labels"]
    assert len(labels) == len(set(labels)), f"duplicate month labels: {labels}"
    assert len(stats_ctx["chart_brutto"]) == len(labels)
    assert sum(stats_ctx["chart_brutto"]) == pytest.approx(stats_ctx["year_summary"]["total_brutto"], abs=len(labels))
