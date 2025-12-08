"""
Unit tests for OB (special pay) calculation.

Tests verify that shifts are assigned correct OB hour amounts and pay
according to the Swedish labor rules defined in the project.
"""

import datetime
import sys
from pathlib import Path

# Add the app directory to the path so we can import schedule, models, etc.
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.core.schedule import (
    determine_shift_for_date,
    calculate_shift_hours,
    calculate_ob_hours,
    calculate_ob_pay,
    build_special_ob_rules_for_year,
)
from app.core.holidays import langfredagen
from app.core.storage import load_settings, load_ob_rules


class TestOBCalculation:
    """Test OB hours and pay calculation for various shifts and dates."""

    def setup_method(self):
        """Load base OB rules before each test."""
        self.ob_rules = load_ob_rules()
        self.settings = load_settings()
        # Rotation starts 2026-01-02
        self.rotation_start = datetime.date(2026, 1, 2)

    def find_person_with_shift_on_date(self, target_code: str, target_date: datetime.date) -> tuple:
        """Find which person (1-10) has the target shift code on the given date.
        
        Returns (person_id, shift, hours, start, end) or raises AssertionError.
        """
        for person_id in range(1, 11):
            shift, _ = determine_shift_for_date(target_date, start_week=person_id)
            if shift and shift.code == target_code:
                hours, start, end = calculate_shift_hours(target_date, shift)
                return (person_id, shift, hours, start, end)
        
        raise AssertionError(f"Could not find person with {target_code} shift on {target_date}")

    def test_regular_weekday_morning_shift_no_ob(self):
        """Morning shift (N1) on a regular weekday should have OB2 from 06:00-07:00 only."""
        # Week 3 of rotation has N1 shifts for multiple people
        # 2026-01-19 to 2026-01-23 is week 3 (Jan 19 is Monday)
        test_date = datetime.date(2027, 1, 19)  # Monday of week 3
        person_id, shift, hours, start, end = self.find_person_with_shift_on_date("N1", test_date)

        assert shift.code == "N1"
        assert hours == 8.5

        ob_hours = calculate_ob_hours(start, end, self.ob_rules)
        # Morning shift 06:00-14:30 has OB2 from 06:00-07:00 (1 hour)
        assert ob_hours["OB2"] == 1.0, f"Expected OB2=1.0, got {ob_hours['OB2']}"
        # No evening OB1 (shift ends at 14:30, before 18:00)
        assert ob_hours["OB1"] == 0.0, f"Expected OB1=0.0, got {ob_hours['OB1']}"

    def test_regular_weekday_evening_shift(self):
        """Evening shift (N2) on a regular weekday should have OB1 from 18:00-22:30."""
        # Week 4 and 7, 8, 10 have N2 shifts
        # 2027-01-26 to 2027-01-30 is week 4
        test_date = datetime.date(2027, 1, 26)  # Monday of week 4
        person_id, shift, hours, start, end = self.find_person_with_shift_on_date("N2", test_date)

        assert shift.code == "N2"
        assert hours == 8.5

        ob_hours = calculate_ob_hours(start, end, self.ob_rules)
        # Evening shift 14:00-22:30 has 4.5h OB1 (18:00-22:30)
        assert ob_hours["OB1"] == 4.5, f"Expected OB1=4.5, got {ob_hours['OB1']}"
        assert ob_hours["OB2"] == 0.0, f"Expected OB2=0.0, got {ob_hours['OB2']}"

    def test_night_shift_spanning_midnight(self):
        """Night shift (N3) 22:00-06:30 (next day) should include OB1 and OB2."""
        # Week 1 and 2 have N3 shifts
        # 2027-01-05 to 2027-01-09 is week 2
        test_date = datetime.date(2027, 1, 5)  # Monday of week 2
        person_id, shift, hours, start, end = self.find_person_with_shift_on_date("N3", test_date)

        assert shift.code == "N3"
        assert hours == 8.5
        # Verify it spans midnight
        assert end.date() == start.date() + datetime.timedelta(days=1), \
            f"Expected to span midnight but start={start.date()}, end={end.date()}"

        ob_hours = calculate_ob_hours(start, end, self.ob_rules)
        # 22:00-24:00 on first day = OB1 (evening, 2 hours)
        # 00:00-06:30 on next day = OB2 (night, 6.5 hours)
        assert ob_hours["OB1"] == 2.0, f"Expected OB1=2.0, got {ob_hours['OB1']}"
        assert ob_hours["OB2"] == 6.5, f"Expected OB2=6.5, got {ob_hours['OB2']}"

    def test_good_friday_shift(self):
        """Shift on Good Friday should be OB5 (storhelg)."""
        year = 2027
        good_friday = langfredagen(year)
        test_date = good_friday

        shift, _ = determine_shift_for_date(test_date, start_week=2)
        if shift is None or shift.code == "OFF":
            # If person is off on Good Friday, test with a different person
            shift, _ = determine_shift_for_date(test_date, start_week=1)

        if shift and shift.code != "OFF":
            hours, start, end = calculate_shift_hours(test_date, shift)

            # Include special OB rules for this year
            special_rules = build_special_ob_rules_for_year(year)
            combined = self.ob_rules + special_rules

            ob_hours = calculate_ob_hours(start, end, combined)
            # All hours on Good Friday should be OB5
            assert ob_hours["OB5"] == hours, \
                f"Expected OB5={hours}, got {ob_hours['OB5']} on Good Friday {test_date}"
            assert ob_hours["OB1"] == 0.0
            assert ob_hours["OB4"] == 0.0

    def test_calculate_ob_pay(self):
        """Test OB pay calculation using monthly salary and rule rates."""
        # Use a Monday from week 4 which has N2 (evening) shifts
        test_date = datetime.date(2027, 1, 26)  # Monday of week 4
        person_id, shift, hours, start, end = self.find_person_with_shift_on_date("N2", test_date)

        ob_pay = calculate_ob_pay(start, end, self.ob_rules, self.settings.monthly_salary)

        # Evening shift 14:00-22:30 has 4.5h OB1
        # OB1 rate is 600 (monthly_salary / 600 per hour)
        expected_ob1_pay = 4.5 * (self.settings.monthly_salary / 600)
        assert ob_pay["OB1"] == expected_ob1_pay, \
            f"Expected OB1 pay={expected_ob1_pay}, got {ob_pay['OB1']}"

    def test_off_shift_no_ob(self):
        """OFF shift should result in zero OB hours and pay."""
        # Week 1 has OFF shifts for person 1-2
        test_date = datetime.date(2027, 1, 5)  # Monday of week 2
        person_id, shift, hours, start, end = self.find_person_with_shift_on_date("OFF", test_date)

        assert shift.code == "OFF"
        assert hours == 0.0
        assert start is None
        assert end is None

        ob_hours = calculate_ob_hours(start, end, self.ob_rules)
        for code in ob_hours:
            assert ob_hours[code] == 0.0, f"Expected {code}=0.0 for OFF shift, got {ob_hours[code]}"

    def test_special_ob_rules_generated(self):
        """Verify that special OB rules are generated for holidays."""
        year = 2027
        special_rules = build_special_ob_rules_for_year(year)
        
        assert len(special_rules) > 0, "No special OB rules generated"
        ob4_rules = [r for r in special_rules if r.code == "OB4"]
        ob5_rules = [r for r in special_rules if r.code == "OB5"]
        assert len(ob4_rules) > 0, "No OB4 (storhelg major) rules generated"
        assert len(ob5_rules) > 0, "No OB5 (storhelg minor) rules generated"


def run_tests():
    """Run all tests and print results."""
    print("\n" + "=" * 60)
    print("Running OB Calculation Tests")
    print("=" * 60 + "\n")

    test_obj = TestOBCalculation()
    test_methods = [
        method
        for method in dir(test_obj)
        if method.startswith("test_") and callable(getattr(test_obj, method))
    ]

    passed = 0
    failed = 0

    for test_name in sorted(test_methods):
        test_obj.setup_method()
        try:
            getattr(test_obj, test_name)()
            print(f"✓ {test_name}")
            passed += 1
        except AssertionError as e:
            print(f"✗ {test_name}")
            print(f"  AssertionError: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
        except Exception as e:
            print(f"✗ {test_name}: Unexpected error: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60 + "\n")

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
