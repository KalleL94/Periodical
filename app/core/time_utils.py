import datetime
import logging
from typing import Any

logger = logging.getLogger(__name__)


def parse_ot_times(ot_shift: Any, date: datetime.date) -> tuple[datetime.datetime, datetime.datetime]:
    """Parse OT shift times and return (start_datetime, end_datetime).

    Handles:
    1) str times: "HH:MM" or "HH:MM:SS"
    2) datetime.time objects
    3) shifts crossing midnight (end <= start => end + 1 day)
    4) error handling via logging + ValueError (no bare except)
    """

    def _to_time(value: Any, field_name: str) -> datetime.time:
        if isinstance(value, datetime.time):
            return value

        if isinstance(value, str):
            s = value.strip()
            if not s:
                logger.error("OT %s is empty string. ot_shift=%r date=%s", field_name, ot_shift, date)
                raise ValueError(f"OT {field_name} is empty")

            try:
                # "HH:MM" or "HH:MM:SS"
                if len(s.split(":")) == 2:
                    return datetime.datetime.strptime(s, "%H:%M").time()
                return datetime.datetime.strptime(s, "%H:%M:%S").time()
            except ValueError as e:
                logger.exception(
                    "Failed parsing OT %s as time string. value=%r ot_shift=%r date=%s",
                    field_name,
                    value,
                    ot_shift,
                    date,
                )
                raise ValueError(f"Invalid OT {field_name} format: {value!r}") from e

        logger.error(
            "Unsupported OT %s type. type=%s value=%r ot_shift=%r date=%s",
            field_name,
            type(value).__name__,
            value,
            ot_shift,
            date,
        )
        raise ValueError(f"Unsupported OT {field_name} type: {type(value).__name__}")

    start_time = _to_time(getattr(ot_shift, "start_time", None), "start_time")
    end_time = _to_time(getattr(ot_shift, "end_time", None), "end_time")

    start_dt = datetime.datetime.combine(date, start_time)
    end_dt = datetime.datetime.combine(date, end_time)

    # Shift crosses midnight
    if end_dt <= start_dt:
        end_dt += datetime.timedelta(days=1)

    return start_dt, end_dt


def subtract_covered(
    start: datetime.datetime,
    end: datetime.datetime,
    covered: list[tuple[datetime.datetime, datetime.datetime]],
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """Return the parts of [start, end) not already claimed by a higher priority rule.

    Both OB and on-call resolve overlapping rules by priority: the highest priority rule
    claims an interval and lower ones only apply to what is left over. Shared by
    app.core.schedule.ob and app.core.oncall so the two cannot drift apart.
    """
    result = []
    cursor = start

    for cov_start, cov_end in sorted(covered):
        if cov_end <= cursor or cov_start >= end:
            continue
        if cov_start > cursor:
            result.append((cursor, min(cov_start, end)))
        cursor = max(cursor, cov_end)

    if cursor < end:
        result.append((cursor, end))

    return result
