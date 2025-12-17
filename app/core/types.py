# app\core\types.py

"""
Custom type definitions for improved type safety across the application.

This module defines NewType wrappers for common domain concepts to prevent
mixing up similar primitive types (e.g., person_id vs year vs week).
"""

from datetime import date, datetime
from typing import NewType, TypedDict

from app.core.models import ShiftType

# Domain-specific type aliases using NewType for type safety
PersonId = NewType("PersonId", int)
Year = NewType("Year", int)
Month = NewType("Month", int)
Day = NewType("Day", int)
Week = NewType("Week", int)
RotationWeek = NewType("RotationWeek", str)
ShiftCode = NewType("ShiftCode", str)

# Type aliases for common structures
Hours = float
MonetaryAmount = float


class DayInfo(TypedDict, total=False):
    """
    Type definition for a single day's data structure.
    """

    date: date
    weekday_index: int
    weekday_name: str
    person_id: PersonId
    person_name: str
    shift: ShiftType | None
    rotation_week: RotationWeek | None
    hours: Hours
    start: datetime | None
    end: datetime | None
    ob: dict[str, Hours]
    persons: list["PersonDayData"]


class PersonDayData(TypedDict, total=False):
    """Type definition for a person's data within a day."""

    person_id: PersonId
    person_name: str
    shift: ShiftType | None
    rotation_week: RotationWeek | None
    hours: Hours
    start: datetime | None
    end: datetime | None


class MonthSummary(TypedDict):
    """Type definition for monthly summary data."""

    person_id: PersonId
    person_name: str
    year: Year
    month: Month
    days: list[DayInfo]
    total_hours: Hours
    shift_counts: dict[str, int]
    ob_hours: dict[str, Hours]
    ob_pay: dict[str, MonetaryAmount]


class YearSummary(TypedDict):
    """Type definition for yearly summary data."""

    total_hours: Hours
    shift_counts: dict[str, int]
    ob_hours: dict[str, Hours]
    ob_pay: dict[str, MonetaryAmount]


class CoworkStats(TypedDict):
    """Type definition for coworker statistics."""

    other_id: PersonId
    other_name: str
    total: int
    by_shift: dict[str, int]


class CoworkDetail(TypedDict):
    """Type definition for detailed coworker shift information."""

    date: date
    weekday_name: str
    rotation_week: RotationWeek | None
    target_id: PersonId
    target_name: str
    target_shift: ShiftType
    other_id: PersonId
    other_name: str
    other_shift: ShiftType


class NavigationDates(TypedDict):
    """Type definition for navigation date information."""

    prev_year: Year
    prev_month: Month | None
    prev_week: Week | None
    prev_day: Day | None
    next_year: Year
    next_month: Month | None
    next_week: Week | None
    next_day: Day | None
