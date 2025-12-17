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

    # Pass över midnatt
    if end_dt <= start_dt:
        end_dt += datetime.timedelta(days=1)

    return start_dt, end_dt
