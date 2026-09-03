"""On-call shifts limited to part of a day, so two people can share one day.

The concrete case: Saturday 12 September 2026 is split between two people, one
taking 00:00-14:00 and the other 14:00-24:00. Together they must be paid exactly
what one full-day on-call shift costs, and each segment must be priced by the
rules that actually cover it.
"""

import datetime
import sys
from pathlib import Path
from types import SimpleNamespace

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ruff: noqa: E402
from app.core.schedule.period import _compute_oncall_pay, oncall_window

SATURDAY = datetime.date(2026, 9, 12)  # OC_WEEKEND_SAT, 97 kr/h all day
FRIDAY = datetime.date(2026, 9, 11)  # OC_WEEKDAY to 17:00, then OC_WEEKEND

OC_SHIFT = SimpleNamespace(code="OC")
SETTINGS = SimpleNamespace(monthly_salary=40000)


def _pay(date, start=None, end=None):
    override = SimpleNamespace(start_time=start, end_time=end) if (start or end) else None
    return _compute_oncall_pay(OC_SHIFT, date, 1, {}, SETTINGS, None, override)


def test_no_window_is_still_the_whole_day():
    pay, details = _pay(SATURDAY)
    assert details["total_hours"] == 24.0
    assert pay == 24 * 97


def test_split_day_pays_each_half_and_sums_to_the_whole():
    morning_pay, morning = _pay(SATURDAY, "00:00", "14:00")
    evening_pay, evening = _pay(SATURDAY, "14:00", "00:00")

    assert morning["total_hours"] == 14.0
    assert morning_pay == 14 * 97
    assert evening["total_hours"] == 10.0
    assert evening_pay == 10 * 97
    assert morning_pay + evening_pay == _pay(SATURDAY)[0]


def test_window_is_priced_by_the_rules_it_actually_covers():
    """A Friday evening window is weekend rate; the same Friday morning is weekday."""
    _, evening = _pay(FRIDAY, "17:00", "00:00")
    _, morning = _pay(FRIDAY, "00:00", "07:00")

    assert evening["breakdown"]["OC_WEEKEND"]["hours"] == 7.0
    assert "OC_WEEKDAY" not in evening["breakdown"]
    assert morning["breakdown"]["OC_WEEKDAY"]["hours"] == 7.0
    assert "OC_WEEKEND" not in morning["breakdown"]


def test_window_bounds_default_to_the_day_edges():
    day_start = datetime.datetime(2026, 9, 12, 0, 0)
    day_end = datetime.datetime(2026, 9, 13, 0, 0)

    assert oncall_window(SATURDAY) == (day_start, day_end)
    assert oncall_window(SATURDAY, SimpleNamespace(start_time=None, end_time=None)) == (day_start, day_end)
    # An end of "00:00" is midnight at the end of the day, not the start of it.
    assert oncall_window(SATURDAY, SimpleNamespace(start_time="14:00", end_time="00:00")) == (
        datetime.datetime(2026, 9, 12, 14, 0),
        day_end,
    )
    # A start with no end runs to the end of the day.
    assert oncall_window(SATURDAY, SimpleNamespace(start_time="06:00", end_time=None))[1] == day_end
