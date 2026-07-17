"""
Tests for canonical iCal generation (build_ical / feed_window).

build_ical is a pure function fed with canonical day dicts from
generate_period_data, so these tests construct day dicts by hand and never
touch the database.
"""

import datetime
from collections import namedtuple
from zoneinfo import ZoneInfo

from icalendar import Calendar

from app.core.calendar_export import add_months, build_ical, feed_window

SWE_TZ = ZoneInfo("Europe/Stockholm")

MockShiftType = namedtuple("ShiftType", ["code", "label", "start_time", "end_time"])

SHIFT_N1 = MockShiftType(code="N1", label="Day Shift", start_time="08:00", end_time="16:00")
SHIFT_N2 = MockShiftType(code="N2", label="Evening Shift", start_time="16:00", end_time="00:00")
SHIFT_OFF = MockShiftType(code="OFF", label="Off", start_time=None, end_time=None)
SHIFT_SEM = MockShiftType(code="SEM", label="Vacation", start_time=None, end_time=None)


def _day(date, shift, hours=8.0, start=None, end=None, **extra):
    if shift is not None and shift.start_time and start is None:
        start = datetime.datetime.combine(date, datetime.time(8, 0), tzinfo=SWE_TZ)
        end = start + datetime.timedelta(hours=8)
    return {"date": date, "shift": shift, "hours": hours, "start": start, "end": end, **extra}


def event_date(event):
    dt = event["dtstart"].dt
    return dt.date() if isinstance(dt, datetime.datetime) else dt


class TestAddMonths:
    def test_forward_and_backward(self):
        assert add_months(datetime.date(2026, 7, 17), 6) == datetime.date(2027, 1, 17)
        assert add_months(datetime.date(2026, 7, 17), -2) == datetime.date(2026, 5, 17)

    def test_backward_across_year_boundary(self):
        assert add_months(datetime.date(2026, 1, 15), -2) == datetime.date(2025, 11, 15)

    def test_day_clamps_to_target_month_length(self):
        assert add_months(datetime.date(2026, 8, 31), 6) == datetime.date(2027, 2, 28)
        assert add_months(datetime.date(2026, 5, 31), -2) == datetime.date(2026, 3, 31)


class TestFeedWindow:
    def test_midyear_reaches_back_to_january_first(self):
        start, end = feed_window(datetime.date(2026, 7, 17))
        assert start == datetime.date(2026, 1, 1)
        assert end == datetime.date(2027, 1, 17)

    def test_january_two_month_rule_reaches_previous_year(self):
        start, end = feed_window(datetime.date(2026, 1, 15))
        assert start == datetime.date(2025, 11, 15)
        assert end == datetime.date(2026, 7, 15)


class TestBuildIcal:
    def test_valid_calendar_with_events(self):
        days = [
            _day(datetime.date(2026, 7, 13), SHIFT_N1),
            _day(datetime.date(2026, 7, 14), SHIFT_OFF, hours=0.0),
            _day(datetime.date(2026, 7, 15), None, hours=0.0),
        ]
        cal = Calendar.from_ical(build_ical(days, user_id=1))
        events = [c for c in cal.walk() if c.name == "VEVENT"]

        assert len(events) == 1
        assert str(events[0]["summary"]) == "Dagpass"

    def test_uid_excludes_shift_code(self):
        days = [_day(datetime.date(2026, 7, 13), SHIFT_N1)]
        cal = Calendar.from_ical(build_ical(days, user_id=7))
        event = next(c for c in cal.walk() if c.name == "VEVENT")

        assert str(event["uid"]) == "2026-07-13_7@periodical"

    def test_uid_stable_across_shift_change(self):
        day_before = [_day(datetime.date(2026, 7, 13), SHIFT_N1)]
        day_after = [_day(datetime.date(2026, 7, 13), SHIFT_N2)]

        def uid(ical):
            return str(next(c for c in Calendar.from_ical(ical).walk() if c.name == "VEVENT")["uid"])

        assert uid(build_ical(day_before, user_id=1)) == uid(build_ical(day_after, user_id=1))

    def test_uid_identical_across_languages(self):
        days = [_day(datetime.date(2026, 7, 13), SHIFT_N1)]

        def uid(ical):
            return str(next(c for c in Calendar.from_ical(ical).walk() if c.name == "VEVENT")["uid"])

        assert uid(build_ical(days, user_id=3, lang="sv")) == uid(build_ical(days, user_id=3, lang="en"))

    def test_untimed_shift_becomes_all_day_event(self):
        days = [_day(datetime.date(2026, 7, 13), SHIFT_SEM, hours=0.0)]
        cal = Calendar.from_ical(build_ical(days, user_id=1))
        event = next(c for c in cal.walk() if c.name == "VEVENT")

        assert event["dtstart"].dt == datetime.date(2026, 7, 13)
        assert event["dtend"].dt == datetime.date(2026, 7, 14)

    def test_masked_employment_days_skipped(self):
        days = [
            _day(datetime.date(2026, 7, 13), SHIFT_N1, before_employment=True),
            _day(datetime.date(2026, 7, 14), SHIFT_N1, after_employment=True),
            _day(datetime.date(2026, 7, 15), SHIFT_N1),
        ]
        cal = Calendar.from_ical(build_ical(days, user_id=1))
        events = [c for c in cal.walk() if c.name == "VEVENT"]

        assert len(events) == 1
        assert event_date(events[0]) == datetime.date(2026, 7, 15)

    def test_english_display_names(self):
        days = [_day(datetime.date(2026, 7, 13), SHIFT_N1)]
        cal = Calendar.from_ical(build_ical(days, user_id=1, lang="en"))
        event = next(c for c in cal.walk() if c.name == "VEVENT")

        assert str(event["summary"]) == "Day shift"

    def test_feed_properties_only_when_as_feed(self):
        days = [_day(datetime.date(2026, 7, 13), SHIFT_N1)]

        feed = build_ical(days, user_id=1, as_feed=True)
        download = build_ical(days, user_id=1, as_feed=False)

        assert "REFRESH-INTERVAL;VALUE=DURATION:PT12H" in feed
        assert "X-PUBLISHED-TTL:PT12H" in feed
        assert "REFRESH-INTERVAL" not in download

    def test_empty_days_yield_valid_empty_calendar(self):
        cal = Calendar.from_ical(build_ical([], user_id=1))

        assert cal.name == "VCALENDAR"
        assert not [c for c in cal.walk() if c.name == "VEVENT"]
