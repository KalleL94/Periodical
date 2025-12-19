"""Generering av iCal-filer för användarscheman."""

import datetime
from typing import TYPE_CHECKING

from icalendar import Calendar, Event

from app.core.schedule.core import (
    calculate_shift_hours,
    determine_shift_for_date,
    # get_shift_types,
)

if TYPE_CHECKING:
    from app.core.models import ShiftType

# Mappning av skiftkoder till svenska namn
SHIFT_NAMES: dict[str, str] = {
    "N1": "Dagpass",
    "N2": "Kvällspass",
    "N3": "Nattpass",
    "OC": "Beredskap",
    "SEM": "Semester",
    "OT": "Övertid",
    "OFF": "Ledig",
}


def _get_shift_display_name(shift: "ShiftType") -> str:
    """Hämtar visningsnamn för ett skift."""
    return SHIFT_NAMES.get(shift.code, shift.label)


def generate_ical(
    person_id: int,
    start_date: datetime.date,
    end_date: datetime.date,
) -> str:
    """
    Genererar en iCal-fil för en persons schema.

    Args:
        person_id: Personens ID (1-10)
        start_date: Första datum i intervallet
        end_date: Sista datum i intervallet

    Returns:
        iCal-formaterad sträng
    """
    # Skapa kalender
    cal = Calendar()
    cal.add("prodid", "-//Periodical Schedule//periodical.app//")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", f"Schema Person {person_id}")
    cal.add("x-wr-timezone", "Europe/Stockholm")

    # Loopa genom varje dag i intervallet
    current_date = start_date
    while current_date <= end_date:
        shift, rotation_week = determine_shift_for_date(current_date, person_id)

        # Skippa om inget skift eller ledig dag
        if shift is None or shift.code == "OFF":
            current_date += datetime.timedelta(days=1)
            continue

        # Skapa event för arbetsdagen
        event = _create_shift_event(current_date, person_id, shift)
        cal.add_component(event)

        current_date += datetime.timedelta(days=1)

    return cal.to_ical().decode("utf-8")


def _create_shift_event(
    date: datetime.date,
    person_id: int,
    shift: "ShiftType",
) -> Event:
    """
    Skapar ett VEVENT för ett skift.

    Args:
        date: Datum för skiftet
        person_id: Personens ID
        shift: Skifttyp-objekt

    Returns:
        icalendar Event-objekt
    """
    event = Event()

    # Hämta visningsnamn
    display_name = _get_shift_display_name(shift)
    event.add("summary", display_name)

    # Generera unik UID
    uid = f"{date.isoformat()}_{person_id}_{shift.code}@periodical"
    event.add("uid", uid)

    # Beräkna start/sluttid
    hours, start_dt, end_dt = calculate_shift_hours(date, shift)

    if start_dt and end_dt:
        # Skift med specifika tider
        event.add("dtstart", start_dt)
        event.add("dtend", end_dt)
    else:
        # Heldagsevent (t.ex. semester utan specifika tider)
        event.add("dtstart", date)
        event.add("dtend", date + datetime.timedelta(days=1))

    # Lägg till beskrivning
    description_parts = [f"Skiftkod: {shift.code}"]
    if hours > 0:
        description_parts.append(f"Arbetstid: {hours:.1f} timmar")
    if shift.start_time and shift.end_time:
        description_parts.append(f"Tid: {shift.start_time} - {shift.end_time}")

    event.add("description", "\n".join(description_parts))

    # Tidsstämpel för när eventet skapades
    event.add("dtstamp", datetime.datetime.now(datetime.utc))

    return event


def generate_ical_for_month(
    person_id: int,
    year: int,
    month: int,
) -> str:
    """
    Genererar iCal för en specifik månad.

    Args:
        person_id: Personens ID
        year: År
        month: Månad (1-12)

    Returns:
        iCal-formaterad sträng
    """
    import calendar

    start_date = datetime.date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end_date = datetime.date(year, month, last_day)

    return generate_ical(person_id, start_date, end_date)


def generate_ical_for_year(
    person_id: int,
    year: int,
) -> str:
    """
    Genererar iCal för ett helt år.

    Args:
        person_id: Personens ID
        year: År

    Returns:
        iCal-formaterad sträng
    """
    start_date = datetime.date(year, 1, 1)
    end_date = datetime.date(year, 12, 31)

    return generate_ical(person_id, start_date, end_date)
