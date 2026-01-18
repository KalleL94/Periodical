# app\core\utils.py
import datetime
from typing import Literal
from zoneinfo import ZoneInfo

ViewType = Literal["day", "week", "month", "year"]

# Application timezone - all "today" calculations use Stockholm time
APP_TIMEZONE = ZoneInfo("Europe/Stockholm")


def get_today() -> datetime.date:
    """
    Returns today's date in Stockholm timezone.

    This ensures consistent "today" calculations regardless of server timezone settings.
    Used throughout the application for:
    - Today highlighting in calendars
    - Default year/month/week selection
    - Vacation calculations
    - Current shift detection

    Returns:
        datetime.date: Today's date in Europe/Stockholm timezone
    """
    return datetime.datetime.now(APP_TIMEZONE).date()


def get_safe_today(rotation_start_date: datetime.date) -> datetime.date:
    """
    Returnerar dagens datum, men aldrig tidigare än rotation_start_date.
    Används för att beräkna default-år och -vecka utan att hamna före schemats start.
    """
    today = get_today()
    return rotation_start_date if today < rotation_start_date else today


def get_navigation_dates(
    view_type: ViewType,
    current_date: datetime.date,
) -> dict[str, int]:
    """
    Beräknar prev/next för olika vyer.

    Keys per view_type:
    - "day":   prev_year, prev_month, prev_day, next_year, next_month, next_day
    - "week":  prev_year, prev_week, next_year, next_week
    - "month": prev_year, prev_month, next_year, next_month
    - "year":  prev_year, next_year

    current_date:
    - day:   själva dagen
    - week:  valfri dag i veckan (t ex måndag)
    - month: valfri dag i månaden (t ex den första)
    - year:  valfri dag i året (t ex 1 januari)
    """
    if view_type == "day":
        prev_date = current_date - datetime.timedelta(days=1)
        next_date = current_date + datetime.timedelta(days=1)
        return {
            "prev_year": prev_date.year,
            "prev_month": prev_date.month,
            "prev_day": prev_date.day,
            "next_year": next_date.year,
            "next_month": next_date.month,
            "next_day": next_date.day,
        }

    if view_type == "week":
        iso_year, iso_week, _ = current_date.isocalendar()
        monday = datetime.date.fromisocalendar(iso_year, iso_week, 1)

        prev_monday = monday - datetime.timedelta(weeks=1)
        next_monday = monday + datetime.timedelta(weeks=1)

        prev_year, prev_week, _ = prev_monday.isocalendar()
        next_year, next_week, _ = next_monday.isocalendar()

        return {
            "prev_year": prev_year,
            "prev_week": prev_week,
            "next_year": next_year,
            "next_week": next_week,
        }

    if view_type == "month":
        year = current_date.year
        month = current_date.month

        first_of_month = current_date.replace(day=1)
        prev_month_date = first_of_month - datetime.timedelta(days=1)
        prev_year = prev_month_date.year
        prev_month = prev_month_date.month

        if month == 12:
            next_year = year + 1
            next_month = 1
        else:
            next_year = year
            next_month = month + 1

        return {
            "prev_year": prev_year,
            "prev_month": prev_month,
            "next_year": next_year,
            "next_month": next_month,
        }

    if view_type == "year":
        year = current_date.year
        return {
            "prev_year": year - 1,
            "next_year": year + 1,
        }

    raise ValueError(f"Unsupported view_type: {view_type}")


def get_ot_shift_display_code(start_time: datetime.datetime | str | None) -> str:
    """
    Maps an overtime shift start time to a display code.

    Returns a shift code that indicates which regular shift the OT aligns with:
    - "N1-OT" for OT starting at 06:00 (aligns with N1 shift)
    - "N2-OT" for OT starting at 14:00 (aligns with N2 shift)
    - "N3-OT" for OT starting at 22:00 (aligns with N3 shift)
    - "OT" for all other start times

    Args:
        start_time: The start time of the OT shift (datetime, string, or None)

    Returns:
        str: The shift code for display purposes
    """
    if not start_time:
        return "OT"

    # Extract hour from start time
    hour = None
    if isinstance(start_time, datetime.datetime):
        hour = start_time.hour
    elif isinstance(start_time, str):
        try:
            # Try to parse hour from string (assumes format contains HH at position 11-13)
            hour = int(str(start_time)[11:13])
        except (ValueError, IndexError):
            pass

    # Map hour to shift code
    if hour == 6:
        return "N1-OT"
    elif hour == 14:
        return "N2-OT"
    elif hour == 22:
        return "N3-OT"
    else:
        return "OT"


def calculate_payment_date(work_year: int, work_month: int) -> datetime.date:
    """
    Calculate the payment date for work performed in a given month.

    Payment is on the 25th of the following month, or the first weekday before
    if the 25th falls on a weekend or red day (public holiday).

    Args:
        work_year: Year when work was performed (e.g., 2025)
        work_month: Month when work was performed (1-12)

    Returns:
        Payment date (25th of next month, or first weekday before)

    Examples:
        - Work in Jan 2026 → Paid Feb 25, 2026
        - Work in Nov 2026 → Paid Dec 25, 2026 (or earlier if red day)
        - Work in Dec 2025 → Paid Jan 25, 2026
        - If 25th is Saturday → Paid on Friday 24th
        - If 25th is Sunday → Paid on Friday 23rd
        - If 25th is Dec 25 (juldagen) → Paid on Dec 22 or earlier
    """
    # Calculate the next month
    if work_month == 12:
        payment_month = 1
        payment_year = work_year + 1
    else:
        payment_month = work_month + 1
        payment_year = work_year

    # Start with the 25th
    payment_date = datetime.date(payment_year, payment_month, 25)

    # Check if 25th is a red day (public holiday)
    # December 25 is always a red day (juldagen)
    if payment_month == 12 and payment_date.day == 25:
        # Move to previous weekday before Christmas
        # Dec 24 is also red (julafton), so go to Dec 23 or earlier
        payment_date = datetime.date(payment_year, 12, 23)
        # If Dec 23 is weekend, move back further
        weekday = payment_date.weekday()
        if weekday == 5:  # Saturday
            payment_date = payment_date - datetime.timedelta(days=1)  # Friday Dec 22
        elif weekday == 6:  # Sunday
            payment_date = payment_date - datetime.timedelta(days=2)  # Friday Dec 22
    else:
        # If weekend, move to previous Friday
        weekday = payment_date.weekday()  # 0=Monday, 5=Saturday, 6=Sunday

        if weekday == 5:  # Saturday
            payment_date = payment_date - datetime.timedelta(days=1)  # Friday
        elif weekday == 6:  # Sunday
            payment_date = payment_date - datetime.timedelta(days=2)  # Friday

    return payment_date
