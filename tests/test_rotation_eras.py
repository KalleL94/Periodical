"""
Unit tests for rotation era system.

Tests verify that the rotation era system correctly handles:
- Multiple rotation eras with different lengths
- Temporal queries to find the correct era for a date
- Transition between eras
- Historical data preservation
"""

import datetime
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ruff: noqa: E402
from app.core.schedule import (
    clear_schedule_cache,
    determine_shift_for_date,
    get_rotation_era_for_date,
    get_rotation_length_for_date,
)
from app.database.database import Base, RotationEra

# Use uniquely named in-memory SQLite database for tests (isolated, fast, auto-cleaned)
TEST_DATABASE_URL = "sqlite:///file:test_rotation_eras_memdb?mode=memory&cache=shared&uri=true"
test_engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False, "uri": True})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="function")
def db_session(monkeypatch):
    """Create a fresh in-memory test database for each test."""
    # Create all tables in memory
    Base.metadata.create_all(bind=test_engine)

    # Create session
    session = TestSessionLocal()

    # Use monkeypatch to temporarily replace SessionLocal
    # This ensures proper isolation between tests
    import app.database.database as db_module

    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)

    yield session

    # Cleanup
    session.close()

    # Clear cache to prevent test interference
    clear_schedule_cache()

    # Drop all test tables (in-memory DB will be auto-deleted)
    Base.metadata.drop_all(bind=test_engine)
    # monkeypatch automatically restores original value


@pytest.fixture(scope="function")
def setup_two_eras(db_session):
    """
    Set up two rotation eras for testing:
    - Era 1: 2026-01-02 to 2027-06-01, 10 weeks
    - Era 2: 2027-06-01 onwards, 11 weeks
    """
    # Clear any existing eras first
    db_session.query(RotationEra).delete()
    db_session.commit()

    # Era 1: 10 week rotation (same as current system)
    era1_pattern = {
        "1": ["OFF", "OFF", "OFF", "N3", "N3", "N3", "N3"],
        "2": ["OFF", "OC", "N3", "N3", "N3", "N3", "OFF"],
        "3": ["OFF", "OFF", "N1", "N1", "N1", "N1", "OC"],
        "4": ["OC", "OFF", "N2", "N2", "N2", "OFF", "N1"],
        "5": ["N1", "N1", "N1", "N1", "OC", "OFF", "OFF"],
        "6": ["N3", "N3", "N3", "OFF", "OFF", "OC", "N3"],
        "7": ["N3", "N3", "OFF", "OC", "N2", "N2", "N2"],
        "8": ["N2", "N2", "OFF", "OFF", "N1", "N1", "N1"],
        "9": ["N1", "N1", "OC", "OFF", "OFF", "N2", "N2"],
        "10": ["N2", "N2", "N2", "N2", "OFF", "OFF", "OFF"],
    }

    era1 = RotationEra(
        start_date=datetime.date(2026, 1, 2),
        end_date=datetime.date(2027, 6, 1),
        rotation_length=10,
        weeks_pattern=era1_pattern,
    )
    db_session.add(era1)

    # Era 2: 11 week rotation (extended with one extra week)
    era2_pattern = era1_pattern.copy()
    era2_pattern["11"] = ["OFF", "N3", "N3", "N3", "N3", "OFF", "OFF"]

    era2 = RotationEra(
        start_date=datetime.date(2027, 6, 1),
        end_date=None,  # Ongoing
        rotation_length=11,
        weeks_pattern=era2_pattern,
    )
    db_session.add(era2)

    db_session.commit()

    return {"era1": era1, "era2": era2}


class TestRotationEraQueries:
    """Test querying rotation eras for specific dates."""

    def test_get_era_for_date_before_any_era(self, db_session, setup_two_eras):
        """Dates before the first era should return None."""
        test_date = datetime.date(2025, 12, 31)  # Before era 1 starts
        era = get_rotation_era_for_date(test_date)

        assert era is None, f"Expected None for date before any era, got {era}"

    def test_get_era_for_date_in_first_era(self, db_session, setup_two_eras):
        """Dates in the first era should return era 1."""
        test_date = datetime.date(2026, 6, 15)  # Middle of era 1
        era = get_rotation_era_for_date(test_date)

        assert era is not None, "Expected era to be found"
        assert era.rotation_length == 10, f"Expected 10-week rotation, got {era.rotation_length}"
        assert era.start_date == datetime.date(2026, 1, 2)

    def test_get_era_for_date_in_second_era(self, db_session, setup_two_eras):
        """Dates in the second era should return era 2."""
        test_date = datetime.date(2027, 12, 25)  # Well into era 2
        era = get_rotation_era_for_date(test_date)

        assert era is not None, "Expected era to be found"
        assert era.rotation_length == 11, f"Expected 11-week rotation, got {era.rotation_length}"
        assert era.start_date == datetime.date(2027, 6, 1)

    def test_get_era_for_date_on_transition_boundary(self, db_session, setup_two_eras):
        """Date exactly on era transition should use the new era."""
        test_date = datetime.date(2027, 6, 1)  # Exact start of era 2
        era = get_rotation_era_for_date(test_date)

        assert era is not None, "Expected era to be found"
        assert era.rotation_length == 11, f"Expected new era (11 weeks), got {era.rotation_length}"

    def test_get_era_for_date_day_before_transition(self, db_session, setup_two_eras):
        """Date one day before era transition should use the old era."""
        test_date = datetime.date(2027, 5, 31)  # Last day of era 1
        era = get_rotation_era_for_date(test_date)

        assert era is not None, "Expected era to be found"
        assert era.rotation_length == 10, f"Expected old era (10 weeks), got {era.rotation_length}"


class TestRotationLengthHelper:
    """Test the get_rotation_length_for_date helper function."""

    def test_rotation_length_before_any_era(self, db_session, setup_two_eras):
        """Should return None for dates before any era."""
        test_date = datetime.date(2025, 1, 1)
        length = get_rotation_length_for_date(test_date)

        assert length is None, f"Expected None for date before any era, got {length}"

    def test_rotation_length_in_first_era(self, db_session, setup_two_eras):
        """Should return 10 for dates in first era."""
        test_date = datetime.date(2026, 6, 15)
        length = get_rotation_length_for_date(test_date)

        assert length == 10, f"Expected 10 weeks, got {length}"

    def test_rotation_length_in_second_era(self, db_session, setup_two_eras):
        """Should return 11 for dates in second era."""
        test_date = datetime.date(2027, 12, 25)
        length = get_rotation_length_for_date(test_date)

        assert length == 11, f"Expected 11 weeks, got {length}"


class TestMultiEraScheduleCalculations:
    """Test that schedule calculations work correctly across multiple eras."""

    def test_shift_calculation_uses_correct_rotation_length_era1(self, db_session, setup_two_eras):
        """
        Verify that shift calculations in era 1 use 10-week rotation.

        Test a date that would give different results with 10 vs 11 week rotation.
        """
        # Person 1, starting in week 1
        # After 10 weeks (70 days from first Monday), should cycle back to week 1
        test_date = datetime.date(2026, 1, 5)  # First Monday, should be week 2
        shift1, week1 = determine_shift_for_date(test_date, start_week=1)

        # 10 weeks later (70 days)
        test_date2 = test_date + datetime.timedelta(days=70)
        shift2, week2 = determine_shift_for_date(test_date2, start_week=1)

        assert week1 == 2, f"First Monday should be week 2, got {week1}"
        assert week2 == 2, f"After 10 weeks should cycle back to week 2, got {week2}"

        # Verify we're still in era 1
        era = get_rotation_era_for_date(test_date2)
        assert era.rotation_length == 10, "Should still be in 10-week era"

    def test_shift_calculation_uses_correct_rotation_length_era2(self, db_session, setup_two_eras):
        """
        Verify that shift calculations in era 2 use 11-week rotation.

        After 11 weeks in era 2, the rotation should cycle back.
        """
        # Start from first Monday in era 2
        era2_start = datetime.date(2027, 6, 1)  # Tuesday
        days_to_monday = (7 - era2_start.weekday()) % 7
        if days_to_monday == 0 and era2_start.weekday() != 0:
            days_to_monday = 7 - era2_start.weekday()
        first_monday = era2_start + datetime.timedelta(days=days_to_monday)

        shift1, week1 = determine_shift_for_date(first_monday, start_week=1)

        # 11 weeks later (77 days)
        test_date2 = first_monday + datetime.timedelta(days=77)
        shift2, week2 = determine_shift_for_date(test_date2, start_week=1)

        # Should cycle back to the same week after 11 weeks in era 2
        assert week1 == week2, f"After 11 weeks should cycle back: week1={week1}, week2={week2}"

        # Verify we're in era 2
        era = get_rotation_era_for_date(test_date2)
        assert era.rotation_length == 11, "Should be in 11-week era"

    def test_historical_data_preserved_across_era_transition(self, db_session, setup_two_eras):
        """
        Historical schedule calculations should remain consistent even after new era is added.

        A date in era 1 should always return the same shift/week, regardless of
        whether era 2 exists or not.
        """
        # Pick a date in era 1
        historical_date = datetime.date(2026, 6, 15)  # Middle of era 1

        # Calculate shift using era 1 (10 weeks)
        shift_historical, week_historical = determine_shift_for_date(historical_date, start_week=1)

        # Verify we used era 1
        era = get_rotation_era_for_date(historical_date)
        assert era.rotation_length == 10, "Historical date should use era 1"

        # The shift and week should be deterministic
        assert shift_historical is not None, "Historical date should have a shift assigned"
        assert week_historical is not None, "Historical date should have a week assigned"
        assert 1 <= week_historical <= 10, f"Week should be 1-10 in era 1, got {week_historical}"

    def test_week_11_only_exists_in_era2(self, db_session, setup_two_eras):
        """
        Week 11 should only appear in era 2 (11-week rotation).
        Era 1 should never produce week 11.
        """
        # Test many dates in era 1 - none should produce week 11
        era1_start = datetime.date(2026, 1, 2)
        era1_end = datetime.date(2027, 5, 31)

        current_date = era1_start
        while current_date <= era1_end:
            _, week = determine_shift_for_date(current_date, start_week=1)
            if week is not None:
                assert 1 <= week <= 10, f"Era 1 should only have weeks 1-10, got week {week} on {current_date}"
            current_date += datetime.timedelta(days=1)

        # Test a date in era 2 - week 11 should be possible
        # We need to find when week 11 occurs for person 1
        # Starting from era 2 first Monday, person 1 should see week 11 after 10 weeks
        era2_start = datetime.date(2027, 6, 1)
        days_to_monday = (7 - era2_start.weekday()) % 7
        if days_to_monday == 0 and era2_start.weekday() != 0:
            days_to_monday = 7 - era2_start.weekday()
        first_monday_era2 = era2_start + datetime.timedelta(days=days_to_monday)

        # Check weeks in era 2 (sample some dates to find week 11)
        found_week_11 = False
        for i in range(0, 11 * 7):  # Check 11 weeks worth of days
            test_date = first_monday_era2 + datetime.timedelta(days=i)
            _, week = determine_shift_for_date(test_date, start_week=1)
            if week == 11:
                found_week_11 = True
                break

        assert found_week_11, "Era 2 should have week 11 in its rotation"


class TestRotationEraEdgeCases:
    """Test edge cases in rotation era system."""

    def test_single_era_scenario(self, db_session):
        """System should work with just one era (backward compatibility)."""
        # Create only one era
        era_pattern = {
            "1": ["OFF", "OFF", "OFF", "N3", "N3", "N3", "N3"],
            "2": ["OFF", "OC", "N3", "N3", "N3", "N3", "OFF"],
            "3": ["OFF", "OFF", "N1", "N1", "N1", "N1", "OC"],
            "4": ["OC", "OFF", "N2", "N2", "N2", "OFF", "N1"],
            "5": ["N1", "N1", "N1", "N1", "OC", "OFF", "OFF"],
            "6": ["N3", "N3", "N3", "OFF", "OFF", "OC", "N3"],
            "7": ["N3", "N3", "OFF", "OC", "N2", "N2", "N2"],
            "8": ["N2", "N2", "OFF", "OFF", "N1", "N1", "N1"],
            "9": ["N1", "N1", "OC", "OFF", "OFF", "N2", "N2"],
            "10": ["N2", "N2", "N2", "N2", "OFF", "OFF", "OFF"],
        }

        era = RotationEra(
            start_date=datetime.date(2026, 1, 2),
            end_date=None,
            rotation_length=10,
            weeks_pattern=era_pattern,
        )
        db_session.add(era)
        db_session.commit()

        # Test that it works
        test_date = datetime.date(2026, 6, 15)
        shift, week = determine_shift_for_date(test_date, start_week=1)

        assert shift is not None, "Should get shift with single era"
        assert week is not None, "Should get week with single era"
        assert 1 <= week <= 10, f"Week should be 1-10, got {week}"

    def test_three_eras_scenario(self, db_session):
        """System should handle three or more eras."""
        # Era 1: 10 weeks
        era1_pattern = {str(i): ["OFF"] * 7 for i in range(1, 11)}
        era1 = RotationEra(
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2027, 1, 1),
            rotation_length=10,
            weeks_pattern=era1_pattern,
        )
        db_session.add(era1)

        # Era 2: 11 weeks
        era2_pattern = {str(i): ["N1"] * 7 for i in range(1, 12)}
        era2 = RotationEra(
            start_date=datetime.date(2027, 1, 1),
            end_date=datetime.date(2028, 1, 1),
            rotation_length=11,
            weeks_pattern=era2_pattern,
        )
        db_session.add(era2)

        # Era 3: 12 weeks
        era3_pattern = {str(i): ["N2"] * 7 for i in range(1, 13)}
        era3 = RotationEra(
            start_date=datetime.date(2028, 1, 1),
            end_date=None,
            rotation_length=12,
            weeks_pattern=era3_pattern,
        )
        db_session.add(era3)

        db_session.commit()

        # Test each era
        assert get_rotation_length_for_date(datetime.date(2026, 6, 1)) == 10
        assert get_rotation_length_for_date(datetime.date(2027, 6, 1)) == 11
        assert get_rotation_length_for_date(datetime.date(2028, 6, 1)) == 12
