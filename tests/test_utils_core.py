"""Unit tests for app.core.utils pure helpers.

These functions are pure (no DB, no clock dependency except get_today) and drive
date navigation, overtime shift labelling and salary payment dates, so their
correctness is directly visible to users. Tests assert against the documented
contract in each docstring.
"""

import datetime

import pytest

from app.core.utils import (
    calculate_payment_date,
    get_navigation_dates,
    get_ot_shift_display_code,
    get_safe_today,
    get_today,
)


class TestGetToday:
    def test_returns_a_date(self):
        assert isinstance(get_today(), datetime.date)


class TestGetSafeToday:
    def test_returns_today_when_after_start(self):
        start = datetime.date(2000, 1, 1)
        assert get_safe_today(start) == get_today()

    def test_clamps_to_start_when_today_is_earlier(self):
        # A far-future rotation start can never be before today.
        future_start = get_today() + datetime.timedelta(days=365)
        assert get_safe_today(future_start) == future_start


class TestGetNavigationDates:
    def test_day_view_neighbours(self):
        result = get_navigation_dates("day", datetime.date(2026, 3, 15))
        assert result == {
            "prev_year": 2026,
            "prev_month": 3,
            "prev_day": 14,
            "next_year": 2026,
            "next_month": 3,
            "next_day": 16,
        }

    def test_day_view_crosses_month_boundary(self):
        result = get_navigation_dates("day", datetime.date(2026, 1, 31))
        assert (result["next_month"], result["next_day"]) == (2, 1)

    def test_week_view_crosses_iso_year_boundary(self):
        # 2025-12-29 is ISO week 1 of 2026; the previous week is week 52 of 2025.
        result = get_navigation_dates("week", datetime.date(2025, 12, 29))
        assert result["prev_year"] == 2025
        assert result["prev_week"] == 52
        assert result["next_year"] == 2026
        assert result["next_week"] == 2

    def test_month_view_december_wraps_to_january(self):
        result = get_navigation_dates("month", datetime.date(2026, 12, 10))
        assert (result["prev_year"], result["prev_month"]) == (2026, 11)
        assert (result["next_year"], result["next_month"]) == (2027, 1)

    def test_month_view_january_wraps_back(self):
        result = get_navigation_dates("month", datetime.date(2026, 1, 10))
        assert (result["prev_year"], result["prev_month"]) == (2025, 12)
        assert (result["next_year"], result["next_month"]) == (2026, 2)

    def test_year_view(self):
        result = get_navigation_dates("year", datetime.date(2026, 6, 1))
        assert result == {"prev_year": 2025, "next_year": 2027}

    def test_unsupported_view_raises(self):
        with pytest.raises(ValueError):
            get_navigation_dates("decade", datetime.date(2026, 1, 1))  # type: ignore[arg-type]


class TestGetOtShiftDisplayCode:
    def test_none_returns_plain_ot(self):
        assert get_ot_shift_display_code(None) == "OT"

    @pytest.mark.parametrize(
        "hour,expected",
        [(6, "N1-OT"), (14, "N2-OT"), (22, "N3-OT"), (9, "OT"), (0, "OT")],
    )
    def test_datetime_maps_by_hour(self, hour, expected):
        dt = datetime.datetime(2026, 1, 1, hour, 0)
        assert get_ot_shift_display_code(dt) == expected

    @pytest.mark.parametrize(
        "iso,expected",
        [
            ("2026-01-01T06:00", "N1-OT"),
            ("2026-01-01T14:30", "N2-OT"),
            ("2026-01-01T22:15", "N3-OT"),
            ("2026-01-01T08:00", "OT"),
        ],
    )
    def test_iso_string_parsed_by_hour(self, iso, expected):
        assert get_ot_shift_display_code(iso) == expected

    def test_unparseable_string_falls_back_to_ot(self):
        assert get_ot_shift_display_code("not-a-timestamp") == "OT"


class TestCalculatePaymentDate:
    def test_weekday_25th_is_used_directly(self):
        # Jan 2026 work -> Feb 25 2026 (Wednesday).
        assert calculate_payment_date(2026, 1) == datetime.date(2026, 2, 25)

    def test_year_rolls_over_for_december_work(self):
        # Dec 2025 work -> Jan 25 2026, which is a Sunday -> Friday Jan 23.
        assert calculate_payment_date(2025, 12) == datetime.date(2026, 1, 23)

    def test_saturday_25th_moves_to_friday(self):
        # Mar 2026 work -> Apr 25 2026 (Saturday) -> Apr 24.
        assert calculate_payment_date(2026, 3) == datetime.date(2026, 4, 24)

    def test_sunday_25th_moves_to_friday(self):
        # Sep 2026 work -> Oct 25 2026 (Sunday) -> Oct 23.
        assert calculate_payment_date(2026, 9) == datetime.date(2026, 10, 23)

    def test_november_work_uses_christmas_rule(self):
        # Nov work -> Dec 25 (juldagen) is red, so payment moves to Dec 23.
        assert calculate_payment_date(2026, 11) == datetime.date(2026, 12, 23)

    def test_christmas_rule_when_dec_23_is_saturday(self):
        # Dec 23 2017 is a Saturday -> move back to Friday Dec 22.
        assert calculate_payment_date(2017, 11) == datetime.date(2017, 12, 22)

    def test_christmas_rule_when_dec_23_is_sunday(self):
        # Dec 23 2018 is a Sunday -> move back two days to Friday Dec 21.
        assert calculate_payment_date(2018, 11) == datetime.date(2018, 12, 21)
