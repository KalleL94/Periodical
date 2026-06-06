"""Unit tests for the pure interval helpers behind on-call (jour) compensation.

These functions implement the priority-replacement geometry that all jour pay
relies on:
- _parse_time: 'HH:MM' parsing incl. the '24:00' midnight sentinel
- _select_oncall_rules_for_date: weekday and specific-date matching
- _get_rule_intervals_for_shift: clipping a rule onto a shift, incl. rules that
  span midnight or end at 24:00
- _subtract_covered_oncall: time not already claimed by higher-priority rules
"""

import datetime

from app.core.oncall import (
    OnCallRule,
    _get_rule_intervals_for_shift,
    _parse_time,
    _select_oncall_rules_for_date,
    _subtract_covered_oncall,
)


def _dt(day, hour, minute=0):
    return datetime.datetime(2026, 1, day, hour, minute)


class TestParseTime:
    def test_regular_time(self):
        assert _parse_time("08:30") == (8, 30)

    def test_midnight_sentinel(self):
        assert _parse_time("24:00") == (24, 0)

    def test_zero(self):
        assert _parse_time("00:00") == (0, 0)


class TestSelectOncallRulesForDate:
    def test_matches_by_weekday(self):
        # 2026-01-05 is a Monday (weekday 0).
        monday = OnCallRule(code="MON", label="m", days=[0], start_time="00:00", end_time="24:00")
        sunday = OnCallRule(code="SUN", label="s", days=[6], start_time="00:00", end_time="24:00")
        selected = _select_oncall_rules_for_date(datetime.date(2026, 1, 5), [monday, sunday])
        assert [r.code for r in selected] == ["MON"]

    def test_matches_by_specific_date(self):
        special = OnCallRule(
            code="SPECIAL", label="x", specific_dates=["2026-01-05"], start_time="00:00", end_time="24:00"
        )
        selected = _select_oncall_rules_for_date(datetime.date(2026, 1, 5), [special])
        assert [r.code for r in selected] == ["SPECIAL"]

    def test_accepts_datetime_as_well_as_date(self):
        monday = OnCallRule(code="MON", label="m", days=[0], start_time="00:00", end_time="24:00")
        selected = _select_oncall_rules_for_date(_dt(5, 12), [monday])
        assert [r.code for r in selected] == ["MON"]

    def test_no_match_returns_empty(self):
        sunday = OnCallRule(code="SUN", label="s", days=[6], start_time="00:00", end_time="24:00")
        assert _select_oncall_rules_for_date(datetime.date(2026, 1, 5), [sunday]) == []


class TestGetRuleIntervalsForShift:
    def test_simple_daytime_window(self):
        rule = OnCallRule(code="D", label="d", days=[0], start_time="08:00", end_time="16:00")
        intervals = _get_rule_intervals_for_shift(rule, _dt(5, 0), _dt(6, 0))
        assert intervals == [(_dt(5, 8), _dt(5, 16))]

    def test_full_day_window_with_24_00_end(self):
        rule = OnCallRule(code="F", label="f", days=[0], start_time="00:00", end_time="24:00")
        intervals = _get_rule_intervals_for_shift(rule, _dt(5, 0), _dt(6, 0))
        assert intervals == [(_dt(5, 0), _dt(6, 0))]

    def test_rule_spanning_midnight(self):
        rule = OnCallRule(code="N", label="n", days=[0], start_time="22:00", end_time="06:00", spans_to_next_day=True)
        # Monday rule across a Mon 00:00 -> Wed 00:00 shift: 22:00 Mon to 06:00 Tue.
        intervals = _get_rule_intervals_for_shift(rule, _dt(5, 0), _dt(7, 0))
        assert intervals == [(_dt(5, 22), _dt(6, 6))]

    def test_ends_at_midnight_rolls_to_next_day(self):
        rule = OnCallRule(code="E", label="e", days=[0], start_time="18:00", end_time="24:00")
        intervals = _get_rule_intervals_for_shift(rule, _dt(5, 0), _dt(7, 0))
        assert intervals == [(_dt(5, 18), _dt(6, 0))]

    def test_rule_clipped_to_shift_boundaries(self):
        # Rule covers all day, but the shift only runs 10:00-14:00.
        rule = OnCallRule(code="F", label="f", days=[0], start_time="00:00", end_time="24:00")
        intervals = _get_rule_intervals_for_shift(rule, _dt(5, 10), _dt(5, 14))
        assert intervals == [(_dt(5, 10), _dt(5, 14))]

    def test_non_matching_weekday_yields_no_intervals(self):
        rule = OnCallRule(code="SUN", label="s", days=[6], start_time="08:00", end_time="16:00")
        assert _get_rule_intervals_for_shift(rule, _dt(5, 0), _dt(6, 0)) == []


class TestSubtractCoveredOncall:
    def test_no_coverage_returns_whole_interval(self):
        assert _subtract_covered_oncall(_dt(1, 0), _dt(1, 10), []) == [(_dt(1, 0), _dt(1, 10))]

    def test_middle_coverage_splits_into_two_gaps(self):
        covered = [(_dt(1, 2), _dt(1, 4))]
        result = _subtract_covered_oncall(_dt(1, 0), _dt(1, 10), covered)
        assert result == [(_dt(1, 0), _dt(1, 2)), (_dt(1, 4), _dt(1, 10))]

    def test_full_coverage_returns_empty(self):
        assert _subtract_covered_oncall(_dt(1, 0), _dt(1, 10), [(_dt(1, 0), _dt(1, 10))]) == []

    def test_leading_coverage_leaves_tail(self):
        covered = [(_dt(1, 0), _dt(1, 6))]
        assert _subtract_covered_oncall(_dt(1, 0), _dt(1, 10), covered) == [(_dt(1, 6), _dt(1, 10))]

    def test_unsorted_and_overlapping_coverage_is_merged(self):
        # Out-of-order, overlapping covers should still leave only the true gaps.
        covered = [(_dt(1, 6), _dt(1, 8)), (_dt(1, 1), _dt(1, 3)), (_dt(1, 2), _dt(1, 4))]
        result = _subtract_covered_oncall(_dt(1, 0), _dt(1, 10), covered)
        assert result == [(_dt(1, 0), _dt(1, 1)), (_dt(1, 4), _dt(1, 6)), (_dt(1, 8), _dt(1, 10))]

    def test_coverage_outside_interval_is_ignored(self):
        covered = [(_dt(2, 0), _dt(2, 5))]  # entirely after the interval
        assert _subtract_covered_oncall(_dt(1, 0), _dt(1, 10), covered) == [(_dt(1, 0), _dt(1, 10))]
