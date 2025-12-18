"""
Unit tests for rotation schedule logic.

Tests verify that the rotation system correctly assigns shifts
based on the rotation pattern, start date, and person offsets.
"""

import datetime
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ruff: noqa: E402
from app.core.schedule import determine_shift_for_date, rotation, rotation_start_date


class TestRotationLogic:
    """Test rotation week calculation and shift assignment."""

    def test_rotation_starts_on_configured_date(self):
        """Rotation should start on the date configured in settings.json."""
        expected_start = datetime.date(2026, 1, 2)  # From settings.json
        assert rotation_start_date == expected_start, (
            f"Expected rotation start {expected_start}, got {rotation_start_date}"
        )

    def test_rotation_length_is_ten_weeks(self):
        """Rotation cycle should be 10 weeks as configured."""
        assert rotation.rotation_length == 10, f"Expected rotation_length=10, got {rotation.rotation_length}"

    def test_first_week_first_person_gets_correct_shift(self):
        """
        First person (start_week=1) on rotation start date should get correct shift.

        Rotation starts 2026-01-02 (Friday).
        Week 1, Friday = index 4 = N3 (night shift)
        """
        test_date = datetime.date(2026, 1, 2)  # Friday, rotation start
        shift, rotation_week = determine_shift_for_date(test_date, start_week=1)

        assert rotation_week == 1, f"Expected week 1, got {rotation_week}"
        assert shift is not None, "Expected shift to be assigned, got None"
        assert shift.code == "N3", f"Expected N3 on first Friday, got {shift.code}"

    def test_first_week_monday_starts_week_2(self):
        """
        Rotation starts Friday 2026-01-02 (week 1 partial).
        First Monday is 2026-01-05 which starts week 2.

        Week 2, Monday = index 0 = OFF (from rotation.json week 2)
        """
        test_date = datetime.date(2026, 1, 5)  # First Monday
        shift, rotation_week = determine_shift_for_date(test_date, start_week=1)

        assert rotation_week == 2, f"Expected week 2, got {rotation_week}"
        assert shift is not None
        assert shift.code == "OFF", f"Expected OFF on Monday of week 2, got {shift.code}"

    def test_third_week_starts_on_next_monday(self):
        """
        Week 3 starts on Monday 2026-01-12 (7 days after first Monday).

        Week 3, Monday = index 0 = OFF (from rotation.json week 3)
        """
        test_date = datetime.date(2026, 1, 12)  # Second Monday
        shift, rotation_week = determine_shift_for_date(test_date, start_week=1)

        assert rotation_week == 3, f"Expected week 3, got {rotation_week}"
        assert shift is not None
        assert shift.code == "OFF", f"Expected OFF on Monday of week 3, got {shift.code}"

    def test_week_three_wednesday_is_n1(self):
        """Week 3, Wednesday should be N1 according to rotation.json."""
        test_date = datetime.date(2026, 1, 14)  # Wednesday of week 3
        shift, rotation_week = determine_shift_for_date(test_date, start_week=1)

        assert rotation_week == 3, f"Expected week 3, got {rotation_week}"
        assert shift is not None
        assert shift.code == "N1", f"Expected N1 on Wednesday of week 3, got {shift.code}"

    def test_rotation_cycles_after_10_weeks(self):
        """
        After 10 weeks, rotation should cycle back.

        2026-01-05 = Week 2 (first Monday)
        Week 2 Monday + 70 days (10 weeks) = 2026-03-16 = Week 2 again (12 % 10 = 2)
        """
        # First Monday (week 2)
        first_monday = datetime.date(2026, 1, 5)
        shift1, week1 = determine_shift_for_date(first_monday, start_week=1)

        # 10 weeks later (70 days)
        ten_weeks_later = first_monday + datetime.timedelta(days=70)
        shift2, week2 = determine_shift_for_date(ten_weeks_later, start_week=1)

        assert week1 == 2, f"Expected week 2, got {week1}"
        assert week2 == 2, f"Expected week 2 after cycle (12 % 10 = 2), got {week2}"
        assert shift1.code == shift2.code, (
            f"Same weekday in same rotation week should have same shift. Got {shift1.code} vs {shift2.code}"
        )

    def test_different_start_weeks_offset_correctly(self):
        """
        Different start_week values should offset the rotation correctly.

        Person with start_week=1 is in week 2 on 2026-01-05 (first Monday).
        Person with start_week=2 is in week 3 on the same date.
        """
        test_date = datetime.date(2026, 1, 5)  # First Monday

        shift_p1, week_p1 = determine_shift_for_date(test_date, start_week=1)
        shift_p2, week_p2 = determine_shift_for_date(test_date, start_week=2)

        assert week_p1 == 2, f"Person 1 should be in week 2, got {week_p1}"
        assert week_p2 == 3, f"Person 2 should be in week 3, got {week_p2}"

        # Week 2 Monday = OFF, Week 3 Monday = OFF (both OFF in this case)
        assert shift_p1.code == "OFF"
        assert shift_p2.code == "OFF"

    def test_start_week_5_offset(self):
        """
        Test that start_week=5 correctly offsets to week 6 of rotation.

        First Monday (2026-01-05) is week 2 for start_week=1.
        For start_week=5, it's week 6. Week 6 Monday = N3 according to rotation.json
        """
        test_date = datetime.date(2026, 1, 5)  # First Monday
        shift, week = determine_shift_for_date(test_date, start_week=5)

        assert week == 6, f"Expected week 6, got {week}"
        assert shift.code == "N3", f"Expected N3 for week 6 Monday, got {shift.code}"

    def test_year_boundary_rotation_continues(self):
        """
        Rotation should continue correctly across year boundaries.

        Test a date in early 2027 and verify rotation is calculated correctly.
        """
        # Calculate weeks from rotation start to 2027-01-04 (Monday)
        test_date = datetime.date(2027, 1, 4)
        shift, week = determine_shift_for_date(test_date, start_week=1)

        # Week should be between 1-10 (valid rotation week)
        assert 1 <= week <= 10, f"Rotation week {week} outside valid range 1-10"
        assert shift is not None, "Expected shift assignment"
        # Verify it's a valid shift code from rotation.json
        valid_codes = {"OFF", "N1", "N2", "N3", "OC"}
        assert shift.code in valid_codes, f"Invalid shift code: {shift.code}"

    def test_leap_year_handling(self):
        """
        Test that rotation calculation works correctly in leap years.

        2028 is a leap year. Test dates around February 29.
        """
        # February 29, 2028 (leap day, Wednesday)
        test_date = datetime.date(2028, 2, 29)
        shift, week = determine_shift_for_date(test_date, start_week=1)

        assert 1 <= week <= 10, f"Rotation week {week} outside valid range"
        assert shift is not None, "Expected shift assignment on leap day"

    def test_all_persons_have_different_weeks_same_date(self):
        """
        All 10 persons should be in different rotation weeks on the same date.

        This verifies that start_week offset works correctly for all persons.
        """
        test_date = datetime.date(2026, 1, 12)  # Random Monday
        weeks = []

        for person_id in range(1, 11):
            _, week = determine_shift_for_date(test_date, start_week=person_id)
            weeks.append(week)

        # All 10 persons should have different rotation weeks
        assert len(set(weeks)) == 10, f"Expected 10 different rotation weeks, got {len(set(weeks))}: {weeks}"
        # Weeks should be exactly 1-10 in some order
        assert set(weeks) == set(range(1, 11)), f"Expected weeks 1-10, got {sorted(weeks)}"

    def test_date_before_rotation_start_returns_none(self):
        """
        Dates before rotation start should return (None, None).

        Rotation starts 2026-01-02, so 2026-01-01 should return (None, None).
        """
        test_date = datetime.date(2026, 1, 1)  # Day before rotation starts
        shift, week = determine_shift_for_date(test_date, start_week=1)

        assert shift is None, f"Expected None shift for date before rotation start, got {shift}"
        assert week is None, f"Expected None week for date before rotation start, got {week}"

    def test_specific_shift_pattern_week_3(self):
        """
        Verify specific shift pattern for week 3.

        Week 3: ["OFF", "OFF", "N1", "N1", "N1", "N1", "OC"]
        """
        # Find a week 3 Monday for person 3
        test_date = datetime.date(2026, 1, 5)  # Week 2 for person 1 (start_week=1)
        # Person 3 (start_week=3) is offset +2, so same date is week 4 for them
        # To get week 3, we need to go back 1 week: 2026-01-05 - 7 days = 2025-12-29
        # But that's before rotation start. Let's use a different date.
        # Actually, let's find when person 1 is in week 1 (rotation start week)
        # 2026-01-02 to 2026-01-04 is week 1 for person 1
        # So for person 3 (offset +2), those dates are week 3
        test_date = datetime.date(2026, 1, 2)  # Friday, rotation start, week 1 for person 1
        # Person 3 (start_week=3) will be in week 3 on this date
        shift_fri, week = determine_shift_for_date(test_date, start_week=3)
        shift_sat, _ = determine_shift_for_date(test_date + datetime.timedelta(days=1), start_week=3)
        shift_sun, _ = determine_shift_for_date(test_date + datetime.timedelta(days=2), start_week=3)

        assert week == 3, f"Expected week 3, got {week}"
        # Week 3 pattern: ["OFF", "OFF", "N1", "N1", "N1", "N1", "OC"]
        # Friday = index 4 = N1
        assert shift_fri.code == "N1", f"Week 3 Friday should be N1, got {shift_fri.code}"
        # Saturday = index 5 = N1
        assert shift_sat.code == "N1", f"Week 3 Saturday should be N1, got {shift_sat.code}"
        # Sunday = index 6 = OC
        assert shift_sun.code == "OC", f"Week 3 Sunday should be OC, got {shift_sun.code}"

    def test_rotation_week_calculation_edge_case(self):
        """
        Test rotation week calculation for various edge cases.

        This ensures the modulo arithmetic works correctly.
        """
        # Test person 10 on first Monday
        # Person 1 is in week 2, so person 10 is in week (2 + 9) % 10 = 11 % 10 = 1
        test_date = datetime.date(2026, 1, 5)  # Week 2 Monday for person 1

        shift, week = determine_shift_for_date(test_date, start_week=10)

        assert week == 1, f"Person 10 should be in week 1 (due to modulo), got {week}"

        # Week 1, Monday = OFF according to rotation.json
        assert shift.code == "OFF", f"Week 1 Monday should be OFF, got {shift.code}"
