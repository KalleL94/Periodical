# app\core\utils.py
import datetime
from typing import Literal

ViewType = Literal["day", "week", "month", "year"]


def get_safe_today(rotation_start_date: datetime.date) -> datetime.date:
    """
    Returnerar dagens datum, men aldrig tidigare än rotation_start_date.
    Används för att beräkna default-år och -vecka utan att hamna före schemats start.
    """
    today = datetime.date.today()
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
