"""Overtime calculations and database handling."""

import datetime

from app.core.constants import OT_RATE_DIVISOR

NEXER_OT_DAY_DIVISOR = 94  # 06:00-20:00 Mon-Fri, not a red day
NEXER_OT_OTHER_DIVISOR = 72


def calculate_overtime_pay(monthly_salary: int, hours: float, ot_hourly_rate: float | None = None) -> float:
    """
    Beräknar övertidsersättning.

    Args:
        monthly_salary: Månadslön i SEK
        hours: Antal övertidstimmar
        ot_hourly_rate: Per-user fixed kr/tim. If None, uses salary / 72.

    Returns:
        Övertidsersättning i SEK
    """
    if ot_hourly_rate is not None:
        return ot_hourly_rate * hours
    return (monthly_salary / OT_RATE_DIVISOR) * hours


def nexer_overtime_pay(monthly_salary: int, start: datetime.datetime, end: datetime.datetime) -> float:
    """Nexer overtime for an interval, priced by clock: salary / 94 for 06:00-20:00 Mon-Fri on a
    non-red day, salary / 72 for everything else. Handles intervals crossing midnight."""
    from app.core.holidays import swedish_red_days

    pay = 0.0
    cursor = start
    while cursor < end:
        midnight = datetime.datetime.combine(cursor.date() + datetime.timedelta(days=1), datetime.time())
        day_start = datetime.datetime.combine(cursor.date(), datetime.time(6))
        day_end = datetime.datetime.combine(cursor.date(), datetime.time(20))
        if cursor < day_start:
            stop = day_start
        elif cursor < day_end:
            stop = day_end
        else:
            stop = midnight
        stop = min(stop, end)
        in_day_window = day_start <= cursor < day_end
        weekday = cursor.weekday() < 5 and cursor.date() not in swedish_red_days(cursor.year)
        divisor = NEXER_OT_DAY_DIVISOR if in_day_window and weekday else NEXER_OT_OTHER_DIVISOR
        pay += (stop - cursor).total_seconds() / 3600 * monthly_salary / divisor
        cursor = stop
    return pay


def get_overtime_rows_for_date(session, user_id: int, date: datetime.date) -> list:
    """Every overtime and extra-time row for a user and date, ordered by id."""
    if not session:
        return []

    from app.database.database import OvertimeShift

    return (
        session.query(OvertimeShift)
        .filter(OvertimeShift.user_id == user_id, OvertimeShift.date == date)
        .order_by(OvertimeShift.id)
        .all()
    )


def preferred_ot_row(rows: list):
    """The overtime row that stands for the day: the called-in one, else the first.

    Extra-time rows are ignored. This is also the row on-call pay is recomputed
    around, since only paid overtime interrupts standby.
    """
    ot = [r for r in rows if r.kind == "ot"]
    return next((r for r in ot if r.side == "full"), ot[0] if ot else None)


def get_overtime_shift_for_date(session, user_id: int, date: datetime.date):
    """The day's primary overtime row, for callers that still want just one."""
    return preferred_ot_row(get_overtime_rows_for_date(session, user_id, date))


def get_overtime_shifts_for_month(
    session,
    user_id: int,
    year: int,
    month: int,
) -> list:
    """
    Hämtar alla övertidspass för en användare under en månad.

    Returns:
        Lista av OvertimeShift
    """
    if not session:
        return []

    from app.database.database import OvertimeShift

    start_date = datetime.date(year, month, 1)
    if month == 12:
        end_date = datetime.date(year + 1, 1, 1)
    else:
        end_date = datetime.date(year, month + 1, 1)

    return (
        session.query(OvertimeShift)
        .filter(
            OvertimeShift.user_id == user_id,
            OvertimeShift.date >= start_date,
            OvertimeShift.date < end_date,
        )
        .all()
    )
