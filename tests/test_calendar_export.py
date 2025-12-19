import datetime
from collections import namedtuple
from unittest.mock import patch
from zoneinfo import ZoneInfo  # <--- YOU MISSED THIS

import pytest
from icalendar import Calendar

from app.core.calendar_export import (
    _create_shift_event,
    _get_shift_display_name,
    generate_ical,
)

# Mock ShiftType as a namedtuple for simplicity
MockShiftType = namedtuple("ShiftType", ["code", "label", "start_time", "end_time"])

# Mock data for shifts
mock_shift_n1 = MockShiftType(code="N1", label="Day Shift", start_time="08:00", end_time="16:00")
mock_shift_off = MockShiftType(code="OFF", label="Off", start_time=None, end_time=None)
mock_shift_sem = MockShiftType(code="SEM", label="Vacation", start_time=None, end_time=None)


@pytest.fixture
def mock_determine_shift():
    """Fixture to mock determine_shift_for_date."""

    def mock_func(date, person_id):
        # Return N1 for weekdays, OFF for weekends, SEM for specific date
        if date.weekday() < 5:  # Mon-Fri
            return mock_shift_n1, 1
        elif date == datetime.date(2023, 1, 7):  # Example SEM day
            return mock_shift_sem, 1
        else:
            return mock_shift_off, 1

    return mock_func


@pytest.fixture
def mock_calculate_hours():
    """Fixture to mock calculate_shift_hours."""

    def mock_func(date, shift):
        if shift.code == "N1":
            start_dt = datetime.datetime.combine(date, datetime.time(8, 0), tzinfo=ZoneInfo("Europe/Stockholm"))
            end_dt = datetime.datetime.combine(date, datetime.time(16, 0), tzinfo=ZoneInfo("Europe/Stockholm"))
            return 8.0, start_dt, end_dt
        elif shift.code == "SEM":
            return 0.0, None, None
        return 0.0, None, None

    return mock_func


class TestCalendarExport:
    @patch("app.core.calendar_export.determine_shift_for_date")
    @patch("app.core.calendar_export.calculate_shift_hours")
    def test_generate_ical_is_valid(self, mock_calc, mock_det):
        """Test that generated iCal is valid and contains VCALENDAR and VEVENT."""
        # Setup mocks
        mock_det.side_effect = lambda date, pid: (mock_shift_n1, 1) if date.weekday() < 5 else (mock_shift_off, 1)
        mock_calc.return_value = (
            8.0,
            datetime.datetime(2023, 1, 2, 8, 0, tzinfo=ZoneInfo("Europe/Stockholm")),
            datetime.datetime(2023, 1, 2, 16, 0, tzinfo=ZoneInfo("Europe/Stockholm")),
        )

        # Generate iCal for a week
        start = datetime.date(2023, 1, 2)  # Monday
        end = datetime.date(2023, 1, 8)  # Sunday
        ical_str = generate_ical(1, start, end)

        # Parse and validate
        cal = Calendar.from_ical(ical_str)
        assert "VCALENDAR" in str(cal)
        events = cal.walk("VEVENT")
        assert len(events) > 0  # Should have events for weekdays

    @patch("app.core.calendar_export.determine_shift_for_date")
    @patch("app.core.calendar_export.calculate_shift_hours")
    def test_correct_number_of_events_for_week(self, mock_calc, mock_det):
        """Test that correct number of events are created for a week (5 weekdays)."""
        # Setup mocks: N1 for Mon-Fri, OFF for Sat-Sun
        mock_det.side_effect = lambda date, pid: (mock_shift_n1, 1) if date.weekday() < 5 else (mock_shift_off, 1)
        mock_calc.return_value = (
            8.0,
            datetime.datetime(2023, 1, 2, 8, 0, tzinfo=ZoneInfo("Europe/Stockholm")),
            datetime.datetime(2023, 1, 2, 16, 0, tzinfo=ZoneInfo("Europe/Stockholm")),
        )

        start = datetime.date(2023, 1, 2)  # Monday
        end = datetime.date(2023, 1, 8)  # Sunday
        ical_str = generate_ical(1, start, end)

        cal = Calendar.from_ical(ical_str)
        events = cal.walk("VEVENT")
        assert len(events) == 5  # Only weekdays

    @patch("app.core.calendar_export.determine_shift_for_date")
    def test_off_days_do_not_create_events(self, mock_det):
        """Test that OFF days do not create events."""
        mock_det.return_value = (mock_shift_off, 1)

        start = datetime.date(2023, 1, 1)
        end = datetime.date(2023, 1, 1)
        ical_str = generate_ical(1, start, end)

        cal = Calendar.from_ical(ical_str)
        events = cal.walk("VEVENT")
        assert len(events) == 0

    def test_shift_name_mapping(self):
        """Test that shift names are mapped correctly."""
        assert _get_shift_display_name(mock_shift_n1, "sv") == "Dagpass"
        assert _get_shift_display_name(mock_shift_n1, "en") == "Day shift"

    @patch("app.core.calendar_export.calculate_shift_hours")
    def test_start_end_times_correct(self, mock_calc):
        """Test that start and end times are set correctly in events."""
        mock_calc.return_value = (
            8.0,
            datetime.datetime(2023, 1, 2, 8, 0, tzinfo=ZoneInfo("Europe/Stockholm")),
            datetime.datetime(2023, 1, 2, 16, 0, tzinfo=ZoneInfo("Europe/Stockholm")),
        )

        event = _create_shift_event(datetime.date(2023, 1, 2), 1, mock_shift_n1, "sv")
        assert event["dtstart"].dt == datetime.datetime(2023, 1, 2, 8, 0, tzinfo=ZoneInfo("Europe/Stockholm"))
        assert event["dtend"].dt == datetime.datetime(2023, 1, 2, 16, 0, tzinfo=ZoneInfo("Europe/Stockholm"))

    @patch("app.core.calendar_export.determine_shift_for_date")
    @patch("app.core.calendar_export.calculate_shift_hours")
    def test_uid_is_unique_per_event(self, mock_calc, mock_det):
        """Test that UIDs are unique per event."""

        # Setup mocks for multiple days
        def det_side_effect(date, pid):
            if date == datetime.date(2023, 1, 2):
                return mock_shift_n1, 1
            elif date == datetime.date(2023, 1, 3):
                return mock_shift_n1, 1
            else:
                return mock_shift_off, 1

        mock_det.side_effect = det_side_effect
        mock_calc.return_value = (
            8.0,
            datetime.datetime(2023, 1, 2, 8, 0, tzinfo=ZoneInfo("Europe/Stockholm")),
            datetime.datetime(2023, 1, 2, 16, 0, tzinfo=ZoneInfo("Europe/Stockholm")),
        )

        start = datetime.date(2023, 1, 2)
        end = datetime.date(2023, 1, 3)
        ical_str = generate_ical(1, start, end)

        cal = Calendar.from_ical(ical_str)
        events = cal.walk("VEVENT")
        uids = [event["uid"] for event in events]
        assert len(uids) == len(set(uids))  # All unique
