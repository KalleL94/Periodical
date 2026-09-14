# app/core/schedule/segments.py
"""A day's worked time as a list of intervals.

period.py resolves a day through a chain of sources (vacation, shift override,
swap, rotation, on-call override, overtime). Each of them used to rebind the same
four scalars (shift, hours, start, end), so a day could hold exactly one worked
interval. This module holds the list those scalars are derived from.

Deriving the scalars instead of storing them is what keeps the day dict
byte-identical, so summary.py, payslip.py, excel_shared.py, statistics.py and
api_v1.py need no change.
"""

import datetime
from dataclasses import dataclass

from app.core.schedule.ob import calculate_ob_hours


@dataclass(frozen=True)
class DaySegment:
    """One worked interval on a day.

    start and end are optional because a stored overtime row with unparseable
    times still contributes its hours, which is what the scalar version did.

    ob_eligible is False for on-call, which carries hours and a time span but no
    OB supplement: its compensation comes from the on-call rules instead.
    """

    start: datetime.datetime | None
    end: datetime.datetime | None
    hours: float
    ob_eligible: bool = True


def segment_hours(segments: list[DaySegment]) -> float:
    """Total worked hours across the segments.

    The 0.0 start value matters: bare sum() returns the int 0 for an empty list,
    and the scalar version this replaces always produced a float.
    """
    return sum((segment.hours for segment in segments), 0.0)


def segment_bounds(
    segments: list[DaySegment],
) -> tuple[datetime.datetime | None, datetime.datetime | None]:
    """The span the segments cover, as (earliest start, latest end).

    Segments without clock times are skipped, and a list holding only those
    yields (None, None) the way an empty list does.
    """
    timed = [s for s in segments if s.start is not None and s.end is not None]
    if not timed:
        return None, None
    return min(s.start for s in timed), max(s.end for s in timed)


def segment_ob(segments: list[DaySegment], rules: list) -> dict[str, float]:
    """OB hours per code, summed across every OB-bearing segment.

    Returns an empty dict when no segment qualifies, which is what the scalar
    version returned for OFF, SEM and on-call days. A day that does qualify gets
    every rule code back, zeros included, because calculate_ob_hours seeds its
    result from the rules it is given.
    """
    eligible = [s for s in segments if s.ob_eligible and s.start is not None and s.end is not None]
    if not eligible:
        return {}
    totals: dict[str, float] = {}
    for segment in eligible:
        for code, hours in calculate_ob_hours(segment.start, segment.end, rules).items():
            totals[code] = totals.get(code, 0.0) + hours
    return totals
