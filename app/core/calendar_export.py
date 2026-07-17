"""iCal generation for user schedules (download and subscription feed).

Events are built from canonical day dicts produced by generate_period_data,
so vacations, swaps and overrides are included - the same data the week,
month and year views show.
"""

import calendar as _calendar
import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event, vDuration

from app.core.schedule.period import generate_period_data, mask_days_to_employment
from app.core.schedule.person_history import get_employment_period

if TYPE_CHECKING:
    from app.database.database import User

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


def _get_shift_display_name(shift, lang: str = "sv") -> str:
    """Returns the display name for a shift in the given language."""
    SHIFT_NAMES = SHIFT_NAMES_SV if lang == "sv" else SHIFT_NAMES_EN
    return SHIFT_NAMES.get(shift.code, shift.label)


def add_months(d: datetime.date, months: int) -> datetime.date:
    """Month arithmetic; the day clamps to the target month's length."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, _calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def feed_window(today: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Date range served by the subscription feed.

    Reaches back to Jan 1 of the current year, but always at least two
    months (so a January fetch still covers the end of the previous year),
    and six months forward.
    """
    start = min(datetime.date(today.year, 1, 1), add_months(today, -2))
    return start, add_months(today, 6)


def generate_ical_for_user(
    user: "User",
    start_date: datetime.date,
    end_date: datetime.date,
    lang: str = "sv",
    session=None,
    as_feed: bool = False,
) -> str:
    """Generates iCal for a user's actual schedule via the canonical path."""
    position = user.rotation_person_id
    emp_start, emp_end = get_employment_period(session, user.id, position)
    days = generate_period_data(
        start_date,
        end_date,
        person_id=position,
        session=session,
        employment_start=emp_start,
    )
    days = mask_days_to_employment(
        days, emp_start or datetime.date.min, emp_end or datetime.date.max, keep_substitute_days=True
    )
    return build_ical(days, user_id=user.id, lang=lang, as_feed=as_feed)


def build_ical(days: list[dict], user_id: int, lang: str = "sv", as_feed: bool = False) -> str:
    """Builds a VCALENDAR string from canonical day dicts.

    Off days, empty days and days masked to outside the user's employment
    are skipped. The UID deliberately excludes the shift code so a changed
    shift replaces its predecessor in subscribing clients instead of
    duplicating it.
    """
    cal = Calendar()
    cal.add("prodid", "-//Periodical Schedule//periodical.app//")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "Periodical schema")
    cal.add("x-wr-timezone", "Europe/Stockholm")
    if as_feed:
        cal.add("refresh-interval", vDuration(datetime.timedelta(hours=12)), parameters={"VALUE": "DURATION"})
        cal.add("x-published-ttl", "PT12H")

    for day in days:
        if day.get("before_employment") or day.get("after_employment"):
            continue
        shift = day.get("shift")
        if shift is None or shift.code == "OFF":
            continue
        cal.add_component(_create_shift_event(day, user_id, shift, lang))

    return cal.to_ical().decode("utf-8")


def _create_shift_event(day: dict, user_id: int, shift, lang: str = "sv") -> Event:
    """Creates a VEVENT for one canonical day dict."""
    date = day["date"]
    event = Event()

    event.add("summary", _get_shift_display_name(shift, lang))
    event.add("uid", f"{date.isoformat()}_{user_id}@periodical")

    start_dt = day.get("start")
    end_dt = day.get("end")
    hours = day.get("hours", 0.0) or 0.0

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
