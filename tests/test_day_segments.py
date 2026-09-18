"""Unit tests for the day segment helpers.

These pin the three derive functions on their own. The proof that they preserve
period.py's behaviour is the characterization suite, not this file.
"""

import datetime
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ruff: noqa: E402
from app.core.schedule.ob import ObRule, calculate_ob_hours
from app.core.schedule.segments import (
    DaySegment,
    segment_bounds,
    segment_hours,
    segment_ob,
)

DAY = datetime.date(2026, 9, 14)  # a Monday


def _dt(hour, minute=0, day=DAY):
    return datetime.datetime.combine(day, datetime.time(hour, minute))


def _evening_rule():
    """OB1 18:00-24:00 on weekdays, the shape app/core/schedule/ob.py expects."""
    return ObRule(
        code="OB1",
        label="Kvall",
        start_time="18:00",
        end_time="24:00",
        rate=50,
        days=[0, 1, 2, 3, 4],
    )


def test_empty_list_derives_the_off_day_scalars():
    assert segment_hours([]) == 0.0
    assert segment_bounds([]) == (None, None)
    assert segment_ob([], [_evening_rule()]) == {}


def test_empty_list_yields_a_float_not_an_int():
    """An OFF day reported 0.0 before this refactor, and bare sum() would return 0."""
    assert isinstance(segment_hours([]), float)


def test_hours_sum_across_segments():
    segments = [
        DaySegment(_dt(6), _dt(14, 30), 8.5),
        DaySegment(_dt(16), _dt(18), 2.0),
    ]
    assert segment_hours(segments) == 10.5


def test_bounds_span_earliest_start_to_latest_end():
    segments = [
        DaySegment(_dt(16), _dt(18), 2.0),
        DaySegment(_dt(6), _dt(14, 30), 8.5),
    ]
    assert segment_bounds(segments) == (_dt(6), _dt(18))


def test_segment_without_clock_times_keeps_its_hours_but_not_the_bounds():
    segments = [DaySegment(None, None, 8.5)]
    assert segment_hours(segments) == 8.5
    assert segment_bounds(segments) == (None, None)


def test_ob_sums_per_code_across_segments():
    rules = [_evening_rule()]
    segments = [
        DaySegment(_dt(18), _dt(20), 2.0),
        DaySegment(_dt(21), _dt(22), 1.0),
    ]
    assert segment_ob(segments, rules)["OB1"] == 3.0


def test_ob_ignores_segments_that_are_not_ob_eligible():
    rules = [_evening_rule()]
    midnight_next = datetime.datetime.combine(DAY + datetime.timedelta(days=1), datetime.time(0, 0))
    segments = [DaySegment(_dt(0), midnight_next, 24.0, ob_eligible=False)]
    assert segment_ob(segments, rules) == {}


def test_ob_of_one_eligible_segment_matches_calculate_ob_hours():
    rules = [_evening_rule()]
    segment = DaySegment(_dt(14), _dt(22, 30), 8.5)
    assert segment_ob([segment], rules) == calculate_ob_hours(_dt(14), _dt(22, 30), rules)
