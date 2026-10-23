"""Nexer overtime: monthly salary / 94 for 06:00-20:00 Mon-Fri (not a red day), / 72 otherwise."""

import datetime
from datetime import date

import pytest

from app.core.schedule import clear_schedule_cache, generate_period_data
from app.core.schedule.overtime import nexer_overtime_pay
from app.database.database import OvertimeShift, RotationEra, User, UserRole, WageType
from tests.conftest import _ROTATION_ERA_PATTERN

SALARY = 35000


def dt(y, m, d, h=0):
    return datetime.datetime(y, m, d, h)


def test_nexer_ot_split_evening():
    pay = nexer_overtime_pay(SALARY, dt(2026, 11, 18, 18), dt(2026, 11, 18, 22))
    assert round(pay, 2) == round(2 * SALARY / 94 + 2 * SALARY / 72, 2)


def test_nexer_ot_weekend_all_72():
    pay = nexer_overtime_pay(SALARY, dt(2026, 11, 21, 8), dt(2026, 11, 21, 16))
    assert round(pay, 2) == round(8 * SALARY / 72, 2)


def test_nexer_ot_red_day_all_72():
    pay = nexer_overtime_pay(SALARY, dt(2026, 5, 14, 8), dt(2026, 5, 14, 16))
    assert round(pay, 2) == round(8 * SALARY / 72, 2)


def test_nexer_ot_crosses_midnight():
    pay = nexer_overtime_pay(SALARY, dt(2026, 11, 20, 22), dt(2026, 11, 21, 2))
    assert round(pay, 2) == round(4 * SALARY / 72, 2)


def test_nexer_ot_daytime_weekday_all_94():
    pay = nexer_overtime_pay(SALARY, dt(2026, 11, 18, 6), dt(2026, 11, 18, 20))
    assert round(pay, 2) == round(14 * SALARY / 94, 2)


@pytest.fixture()
def ot_env(test_db):
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
        id=1,
        username="otuser",
        password_hash="x",
        name="OT User",
        role=UserRole.USER,
        wage=SALARY,
        wage_type=WageType.MONTHLY,
        person_id=1,
        vacation={},
        must_change_password=0,
        is_active=1,
    )
    test_db.add(user)
    test_db.add(
        OvertimeShift(
            user_id=1,
            date=date(2026, 11, 18),
            start_time=datetime.time(18, 0),
            end_time=datetime.time(22, 0),
            hours=4.0,
            ot_pay=0.0,
            kind="ot",
            side="full",
        )
    )
    test_db.commit()
    yield test_db, user
    clear_schedule_cache()


def _ot_day(session):
    days = generate_period_data(date(2026, 11, 18), date(2026, 11, 18), person_id=1, session=session)
    return days[0]


def test_ica_ot_unchanged(ot_env):
    session, _ = ot_env
    assert round(_ot_day(session)["ot_pay"], 2) == round(4 * SALARY / 72, 2)


def test_nexer_ot_through_period(ot_env):
    session, user = ot_env
    user.employer_profile = "nexer"
    session.commit()
    day = _ot_day(session)
    expected = 2 * SALARY / 94 + 2 * SALARY / 72
    assert round(day["ot_pay"], 2) == round(expected, 2)
    assert round(day["ot_details"]["hourly_rate"], 4) == round(expected / 4, 4)


def test_nexer_hourly_wage_keeps_hourly_rate(ot_env):
    session, user = ot_env
    user.employer_profile = "nexer"
    user.wage_type = WageType.HOURLY
    user.wage = 200
    session.commit()
    assert round(_ot_day(session)["ot_pay"], 2) == 4 * 200


def test_nexer_ot_rate_override_wins(ot_env):
    session, user = ot_env
    user.employer_profile = "nexer"
    session.commit()
    from app.core.rates import get_user_rates

    rates = {**get_user_rates(user, session=session, effective_date=date(2026, 11, 18)), "ot": 300.0}
    days = generate_period_data(
        date(2026, 11, 18), date(2026, 11, 18), person_id=1, session=session, user_rates_map={1: rates}
    )
    assert days[0]["ot_pay"] == 4 * 300
