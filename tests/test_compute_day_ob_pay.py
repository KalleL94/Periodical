"""Unit tests for compute_day_ob_pay, the shared OB gate used by both the
month/year summary (_process_day_for_summary) and the personal day view
(issue #206). The helper must agree exactly with calculate_ob_hours and
calculate_ob_pay for workable shifts, including shifts crossing midnight,
honour a manual ob_hours_override, and yield zero OB for OFF/OC/OT days.
"""

import datetime

from app.core.models import ShiftType
from app.core.schedule import (
    calculate_ob_hours,
    calculate_ob_pay,
    compute_day_ob_pay,
    get_combined_rules_for_year,
)
from app.core.schedule.ob import apply_ob_hours_override, calculate_ob_hours_by_day

_SALARY = 30000


def _shift(code, start="14:00", end="22:30"):
    return ShiftType(code=code, label=code, start_time=start, end_time=end, color="#000")


def _day(code, start_dt, end_dt, ob_hours_override=None):
    day = {"shift": _shift(code) if code else None, "start": start_dt, "end": end_dt}
    if ob_hours_override is not None:
        day["ob_hours_override"] = ob_hours_override
    return day


def _rules():
    return get_combined_rules_for_year(2026)


def test_matches_calculate_ob_for_evening_shift():
    d = datetime.date(2026, 3, 6)  # Friday: N2 evening reaches OB
    start = datetime.datetime.combine(d, datetime.time(14, 0))
    end = datetime.datetime.combine(d, datetime.time(22, 30))
    rules = _rules()

    ob_hours, ob_pay, ob_by_day = compute_day_ob_pay(_day("N2", start, end), rules, _SALARY)

    assert ob_hours == calculate_ob_hours(start, end, rules)
    assert ob_pay == calculate_ob_pay(start, end, rules, _SALARY, rate_overrides=None)
    assert ob_by_day == calculate_ob_hours_by_day(start, end, rules)
    assert sum(ob_hours.values()) > 0


def test_matches_calculate_ob_for_midnight_crossing_shift():
    d = datetime.date(2026, 3, 7)  # Saturday night N3 crossing into Sunday
    start = datetime.datetime.combine(d, datetime.time(22, 0))
    end = datetime.datetime.combine(d + datetime.timedelta(days=1), datetime.time(6, 30))
    rules = _rules()

    ob_hours, ob_pay, ob_by_day = compute_day_ob_pay(_day("N3", start, end), rules, _SALARY)

    assert ob_hours == calculate_ob_hours(start, end, rules)
    assert ob_pay == calculate_ob_pay(start, end, rules, _SALARY, rate_overrides=None)
    assert ob_by_day == calculate_ob_hours_by_day(start, end, rules)
    # Midnight crossing must split hours over both calendar days
    assert len(ob_by_day) == 2


def test_rate_overrides_are_passed_through():
    d = datetime.date(2026, 3, 6)
    start = datetime.datetime.combine(d, datetime.time(14, 0))
    end = datetime.datetime.combine(d, datetime.time(22, 30))
    rules = _rules()
    overrides = {code: 100.0 for code in calculate_ob_hours(start, end, rules)}

    _, ob_pay, _ = compute_day_ob_pay(_day("N2", start, end), rules, _SALARY, ob_rate_overrides=overrides)

    assert ob_pay == calculate_ob_pay(start, end, rules, _SALARY, rate_overrides=overrides)


def test_manual_override_wins_over_shift_times():
    d = datetime.date(2026, 3, 6)
    start = datetime.datetime.combine(d, datetime.time(14, 0))
    end = datetime.datetime.combine(d, datetime.time(22, 30))
    rules = _rules()
    override = {"OB1": 2.0, "OB3": 1.5}

    ob_hours, ob_pay, ob_by_day = compute_day_ob_pay(_day("N2", start, end, override), rules, _SALARY)

    exp_hours, exp_pay = apply_ob_hours_override(override, _SALARY, rules, None)
    assert ob_hours == exp_hours
    assert ob_pay == exp_pay
    assert ob_by_day == {}


def test_off_oc_ot_and_missing_times_yield_zero_ob():
    d = datetime.date(2026, 3, 6)
    start = datetime.datetime.combine(d, datetime.time(14, 0))
    end = datetime.datetime.combine(d, datetime.time(22, 30))
    rules = _rules()

    for day in (
        _day("OFF", None, None),
        _day("OC", start, end),
        _day("OT", start, end),
        _day("N2", None, None),  # e.g. a full-day absence carries no times
        {"shift": None, "start": None, "end": None},
    ):
        ob_hours, ob_pay, ob_by_day = compute_day_ob_pay(day, rules, _SALARY)
        assert all(v == 0.0 for v in ob_hours.values())
        assert all(v == 0.0 for v in ob_pay.values())
        assert ob_by_day == {}
        assert set(ob_hours) == {r.code for r in rules}
