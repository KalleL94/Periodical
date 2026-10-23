"""On-call is priced per day from the dated profile table, also without a rates map."""

import datetime

import pytest

from app.core.rates import get_user_rates
from app.core.schedule import clear_schedule_cache, generate_month_data, generate_period_data
from app.database.database import OnCallOverride, OnCallOverrideType, RotationEra, User, UserRole, WageType
from tests.conftest import _ROTATION_ERA_PATTERN

# ICA moved from 75/97/112/192 to 78/100/116/198 kr/h on 2026-05-01. 2026-05-02 is a
# Saturday, but it sits inside the 1 May holiday block (Fri to Mon 07:00), so it is
# priced as OC_HOLIDAY. 2026-05-09 is the first plain Saturday after the change.
APR_SAT = datetime.date(2026, 4, 25)
MAY_HOLIDAY_SAT = datetime.date(2026, 5, 2)
MAY_SAT = datetime.date(2026, 5, 9)


@pytest.fixture()
def oc_env(test_db):
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
        username="ocuser",
        password_hash="x",
        name="OC User",
        role=UserRole.USER,
        wage=30000,
        wage_type=WageType.MONTHLY,
        person_id=1,
        vacation={},
        must_change_password=0,
        is_active=1,
    )
    test_db.add(user)
    for day in (APR_SAT, MAY_HOLIDAY_SAT, MAY_SAT):
        test_db.add(OnCallOverride(user_id=1, date=day, override_type=OnCallOverrideType.ADD))
    test_db.commit()
    yield test_db, user
    clear_schedule_cache()


def _oc_pay(days, day):
    return next(d for d in days if d["date"] == day)["oncall_pay"]


def test_period_crossing_rate_change_prices_each_day_at_its_own_rate(oc_env):
    session, user = oc_env
    rates = get_user_rates(user, session=session, effective_date=APR_SAT)
    days = generate_period_data(APR_SAT, MAY_SAT, person_id=1, session=session, user_rates_map={1: rates})
    assert _oc_pay(days, APR_SAT) == 24 * 97
    assert _oc_pay(days, MAY_HOLIDAY_SAT) == 24 * 116
    assert _oc_pay(days, MAY_SAT) == 24 * 100


def test_no_rates_map_prices_on_dated_ica_rates(oc_env):
    session, _ = oc_env
    days = generate_period_data(MAY_SAT, MAY_SAT, person_id=1, session=session)
    assert _oc_pay(days, MAY_SAT) == 24 * 100
    month = generate_month_data(2026, 5, 1, session=session)
    assert _oc_pay(month, MAY_HOLIDAY_SAT) == 24 * 116
    assert _oc_pay(month, MAY_SAT) == 24 * 100


def test_custom_oncall_rate_wins_per_day(oc_env):
    session, user = oc_env
    user.custom_rates = {"oncall": {"OC_WEEKEND_SAT": 50}}
    session.commit()
    rates = get_user_rates(user, session=session, effective_date=MAY_SAT)
    days = generate_period_data(APR_SAT, MAY_SAT, person_id=1, session=session, user_rates_map={1: rates})
    assert _oc_pay(days, APR_SAT) == 24 * 50
    assert _oc_pay(days, MAY_SAT) == 24 * 50


def test_agiremus_profile_keeps_old_rate_after_change(oc_env):
    session, user = oc_env
    user.employer_profile = "agiremus"
    session.commit()
    rates = get_user_rates(user, session=session, effective_date=MAY_SAT)
    days = generate_period_data(MAY_SAT, MAY_SAT, person_id=1, session=session, user_rates_map={1: rates})
    assert _oc_pay(days, MAY_SAT) == 24 * 97
