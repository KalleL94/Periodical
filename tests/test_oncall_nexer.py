"""Nexer on-call rules: four tiers priced as monthly salary / divisor."""

import datetime
from datetime import date

import pytest

from app.core.oncall import (
    build_oncall_rules_for_year,
    calculate_oncall_pay,
    calculate_oncall_pay_for_period,
    get_oncall_rules,
    rule_set_for,
)
from app.core.schedule import clear_schedule_cache, generate_period_data
from app.database.database import OnCallOverride, OnCallOverrideType, RotationEra, User, UserRole, WageType
from tests.conftest import _ROTATION_ERA_PATTERN

SALARY = 35000


def _hours(day: date) -> dict[str, float]:
    breakdown = calculate_oncall_pay(day, SALARY, get_oncall_rules(day.year, "nexer"), {})["breakdown"]
    return {code: b["hours"] for code, b in breakdown.items()}


def test_nexer_friday_split():
    b = calculate_oncall_pay(date(2026, 11, 13), SALARY, get_oncall_rules(2026, "nexer"), {})["breakdown"]
    assert b["NX_WEEKDAY"]["hours"] == 18 and b["NX_NIGHT"]["hours"] == 6
    assert round(b["NX_WEEKDAY"]["pay"], 2) == round(18 * SALARY / 1400, 2)
    assert round(b["NX_NIGHT"]["pay"], 2) == round(6 * SALARY / 900, 2)


def test_nexer_saturday_split():
    assert _hours(date(2026, 11, 14)) == {"NX_NIGHT": 7, "NX_WEEKEND": 17}


def test_nexer_sunday_is_weekend():
    assert _hours(date(2026, 11, 15)) == {"NX_WEEKEND": 24}


def test_nexer_monday_morning_is_weekday():
    assert _hours(date(2026, 11, 16)) == {"NX_WEEKDAY": 24}


def test_nexer_kristi_himmelsfard():
    assert _hours(date(2026, 5, 13)) == {"NX_WEEKDAY": 18, "NX_NIGHT": 6}
    assert _hours(date(2026, 5, 14)) == {"NX_NIGHT": 7, "NX_WEEKEND": 17}
    assert _hours(date(2026, 5, 15)) == {"NX_WEEKDAY": 18, "NX_NIGHT": 6}


def test_nexer_christmas_block():
    assert _hours(date(2026, 12, 24)) == {"NX_WEEKDAY": 7, "NX_SPECIAL": 17}
    for day in (25, 26, 27):
        assert _hours(date(2026, 12, day)) == {"NX_SPECIAL": 24}
    assert _hours(date(2026, 12, 28)) == {"NX_WEEKDAY": 24}


def test_nexer_easter_block():
    assert _hours(date(2026, 4, 2)) == {"NX_WEEKDAY": 18, "NX_SPECIAL": 6}
    assert _hours(date(2026, 4, 6)) == {"NX_SPECIAL": 24}
    assert _hours(date(2026, 4, 7)) == {"NX_WEEKDAY": 24}


def test_nexer_new_year_block_from_previous_year():
    # 2026-12-31 is a Thursday: the block runs to Monday 2027-01-04.
    assert _hours(date(2026, 12, 31)) == {"NX_WEEKDAY": 7, "NX_SPECIAL": 17}
    for day in (1, 2, 3):
        assert _hours(date(2027, 1, day)) == {"NX_SPECIAL": 24}
    assert _hours(date(2027, 1, 4)) == {"NX_WEEKDAY": 24}


def test_nexer_friday_holiday_weekend():
    # 1 May 2026 is a Friday: the weekend tier covers the whole weekend after it.
    assert _hours(date(2026, 4, 30)) == {"NX_WEEKDAY": 18, "NX_NIGHT": 6}
    assert _hours(date(2026, 5, 1)) == {"NX_NIGHT": 7, "NX_WEEKEND": 17}
    assert _hours(date(2026, 5, 2)) == {"NX_WEEKEND": 24}
    assert _hours(date(2026, 5, 4)) == {"NX_WEEKDAY": 24}


def test_nexer_tier_divisors():
    rules = get_oncall_rules(2026, "nexer")
    divisors = {(r.code, r.rate, r.priority, r.fixed_hourly_rate) for r in rules}
    assert divisors == {
        ("NX_WEEKDAY", 1400, 1, None),
        ("NX_NIGHT", 900, 2, None),
        ("NX_WEEKEND", 600, 3, None),
        ("NX_SPECIAL", 350, 4, None),
    }


@pytest.mark.parametrize(
    "rule_set, year",
    [("nexer", 2026), ("nexer", 2027), ("nexer", 2030), ("nexer", 2033), ("ica", 2026), ("ica", 2035)],
)
def test_period_function_agrees_with_day_function(rule_set, year):
    """The OT path prices through calculate_oncall_pay_for_period. It must split hours
    exactly like calculate_oncall_pay. ICA 2035-05-02 (morning after 1 May, eve of
    Kristi himmelsfard) used to lose its evening to the weekday tier."""
    rules = get_oncall_rules(year, rule_set)
    day = date(year, 1, 1)
    while day.year == year:
        whole = calculate_oncall_pay(day, SALARY, rules, {})["breakdown"]
        midnight = datetime.datetime.combine(day, datetime.time(0, 0))
        parts: dict[str, float] = {}
        for start, end in ((0, 9), (9, 24)):
            period = calculate_oncall_pay_for_period(
                midnight + datetime.timedelta(hours=start),
                midnight + datetime.timedelta(hours=end),
                SALARY,
                rules,
                {},
            )
            for code, b in period["breakdown"].items():
                parts[code] = parts.get(code, 0.0) + b["hours"]
        assert parts == {code: b["hours"] for code, b in whole.items()}, day
        assert sum(parts.values()) == 24, day
        day += datetime.timedelta(days=1)


def test_ica_rules_unchanged_by_nexer_cache():
    get_oncall_rules(2026, "nexer")
    assert get_oncall_rules(2026, "ica") == build_oncall_rules_for_year(2026)
    assert get_oncall_rules(2026) == build_oncall_rules_for_year(2026)


def test_rule_set_for():
    assert rule_set_for("nexer") == "nexer"
    for profile in ("ica", "agiremus", "experis", None, ""):
        assert rule_set_for(profile) == "ica"


# --- Schedule generation picks the rule set per user -----------------------------

OC_FRIDAY = date(2026, 5, 8)
OC_SATURDAY = date(2026, 5, 9)


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
        wage=SALARY,
        wage_type=WageType.MONTHLY,
        person_id=1,
        vacation={},
        must_change_password=0,
        is_active=1,
    )
    test_db.add(user)
    for day in (OC_FRIDAY, OC_SATURDAY):
        test_db.add(OnCallOverride(user_id=1, date=day, override_type=OnCallOverrideType.ADD))
    test_db.commit()
    yield test_db, user
    clear_schedule_cache()


def _day(days, day):
    return next(d for d in days if d["date"] == day)


def test_period_uses_profile_rule_set(oc_env):
    from app.core.rates import get_user_rates

    session, user = oc_env
    user.employer_profile = "nexer"
    session.commit()
    rates = get_user_rates(user, session=session, effective_date=OC_FRIDAY)
    days = generate_period_data(OC_FRIDAY, OC_SATURDAY, person_id=1, session=session, user_rates_map={1: rates})
    friday = _day(days, OC_FRIDAY)
    assert {c: b["hours"] for c, b in friday["oncall_details"]["breakdown"].items()} == {
        "NX_WEEKDAY": 18,
        "NX_NIGHT": 6,
    }
    assert friday["oncall_pay"] == round(18 * SALARY / 1400 + 6 * SALARY / 900, 2)


def test_nexer_without_rates_map_resolves_holder_profile(oc_env):
    session, user = oc_env
    user.employer_profile = "nexer"
    session.commit()
    days = generate_period_data(OC_SATURDAY, OC_SATURDAY, person_id=1, session=session)
    breakdown = _day(days, OC_SATURDAY)["oncall_details"]["breakdown"]
    assert set(breakdown) == {"NX_NIGHT", "NX_WEEKEND"}
    assert _day(days, OC_SATURDAY)["oncall_pay"] == round(7 * SALARY / 900 + 17 * SALARY / 600, 2)


def test_agiremus_without_rates_map_prices_on_its_own_table(oc_env):
    session, user = oc_env
    user.employer_profile = "agiremus"
    session.commit()
    days = generate_period_data(OC_SATURDAY, OC_SATURDAY, person_id=1, session=session)
    assert _day(days, OC_SATURDAY)["oncall_pay"] == 24 * 97


def test_nexer_overtime_and_hour_override_use_nexer_rules(oc_env):
    from app.database.database import DayPayOverride, OvertimeShift

    session, user = oc_env
    user.employer_profile = "nexer"
    session.add(
        OvertimeShift(
            user_id=1,
            date=OC_FRIDAY,
            start_time=datetime.time(10, 0),
            end_time=datetime.time(14, 0),
            hours=4.0,
            ot_pay=0.0,
            kind="ot",
            side="full",
        )
    )
    session.add(DayPayOverride(user_id=1, date=OC_SATURDAY, oncall_hours_override={"NX_SPECIAL": 2.0}))
    session.commit()
    days = generate_period_data(OC_FRIDAY, OC_SATURDAY, person_id=1, session=session)
    friday = _day(days, OC_FRIDAY)["oncall_details"]["breakdown"]
    assert {c: b["hours"] for c, b in friday.items()} == {"NX_WEEKDAY": 14, "NX_NIGHT": 6}
    saturday = _day(days, OC_SATURDAY)
    assert saturday["oncall_pay"] == round(2 * SALARY / 350, 2)
