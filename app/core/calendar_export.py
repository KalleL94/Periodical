"""iCal file generation for user schedules."""

import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

from app.core.schedule.core import (
    calculate_shift_hours,
    determine_shift_for_date,
)

if TYPE_CHECKING:
    from app.core.models import ShiftType

# Swedish timezone
SWE_TZ = ZoneInfo("Europe/Stockholm")

# Shift code to Swedish display name mapping
SHIFT_NAMES_SV: dict[str, str] = {
    "N1": "Dagpass",
    "N2": "Kvällspass",
    "N3": "Nattpass",
    "OC": "Beredskap",
    "SEM": "Semester",
    "OT": "Övertid",
    "OFF": "Ledig",
}

# Shift code to English display name mapping
SHIFT_NAMES_EN: dict[str, str] = {
    "N1": "Day shift",
    "N2": "Evening shift",
    "N3": "Night shift",
    "OC": "On-call",
    "SEM": "Vacation",
    "OT": "Overtime",
    "OFF": "Off day",
}


def _get_shift_display_name(shift: "ShiftType", lang: str = "sv") -> str:
    """Returns the display name for a shift in the given language."""
    SHIFT_NAMES = SHIFT_NAMES_SV if lang == "sv" else SHIFT_NAMES_EN
    return SHIFT_NAMES.get(shift.code, shift.label)


def generate_ical(
    person_id: int,
    start_date: datetime.date,
    end_date: datetime.date,
    lang: str = "sv",
) -> str:
    """
    Generates an iCal file for a person's schedule.

    Args:
        person_id: Person ID (1-10)
        start_date: First date in the range
        end_date: Last date in the range

    Returns:
        iCal-formatted string
    """
    cal = Calendar()
    cal.add("prodid", "-//Periodical Schedule//periodical.app//")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", f"Schema Person {person_id}")
    cal.add("x-wr-timezone", "Europe/Stockholm")

    current_date = start_date
    while current_date <= end_date:
        shift, rotation_week = determine_shift_for_date(current_date, person_id)

        # Skip off days
        if shift is None or shift.code == "OFF":
            current_date += datetime.timedelta(days=1)
            continue

        event = _create_shift_event(current_date, person_id, shift, lang)
        cal.add_component(event)

        current_date += datetime.timedelta(days=1)

    return cal.to_ical().decode("utf-8")


def _create_shift_event(
    date: datetime.date,
    person_id: int,
    shift: "ShiftType",
    lang: str = "sv",
) -> Event:
    """
    Creates a VEVENT for a shift.

    Args:
        date: Shift date
        person_id: Person ID
        shift: ShiftType object

    Returns:
        icalendar Event object
    """
    event = Event()

    display_name = _get_shift_display_name(shift, lang)
    event.add("summary", display_name)

    uid = f"{date.isoformat()}_{person_id}_{shift.code}@periodical"
    event.add("uid", uid)

    hours, start_dt, end_dt = calculate_shift_hours(date, shift)

    if start_dt and end_dt:
        event.add("dtstart", start_dt)
        event.add("dtend", end_dt)
    else:
        # All-day event (e.g. vacation with no specific times)
        event.add("dtstart", date)
        event.add("dtend", date + datetime.timedelta(days=1))

    description_parts = [f"Skiftkod: {shift.code}"]
    if hours > 0:
        description_parts.append(f"Arbetstid: {hours:.1f} timmar")
    if shift.start_time and shift.end_time:
        description_parts.append(f"Tid: {shift.start_time} - {shift.end_time}")

    event.add("description", "\n".join(description_parts))

    event.add("dtstamp", datetime.datetime.now(SWE_TZ))

    return event


def generate_ical_for_month(
    person_id: int,
    year: int,
    month: int,
    lang: str = "sv",
) -> str:
    """
    Generates iCal for a specific month.

    Args:
        person_id: Person ID
        year: Year
        month: Month (1-12)

    Returns:
        iCal-formatted string
    """
    import calendar

    start_date = datetime.date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end_date = datetime.date(year, month, last_day)

    return generate_ical(person_id, start_date, end_date, lang)


def generate_ical_for_year(
    person_id: int,
    year: int,
    lang: str = "sv",
) -> str:
    """
    Generates iCal for an entire year.

    Args:
        person_id: Person ID
        year: Year

    Returns:
        iCal-formatted string
    """
    start_date = datetime.date(year, 1, 1)
    end_date = datetime.date(year, 12, 31)

    return generate_ical(person_id, start_date, end_date, lang)
