"""Overtime calculations and database handling."""

import datetime

from app.core.constants import OT_RATE_DIVISOR


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


def get_overtime_shift_for_date(session, user_id: int, date: datetime.date):
    """The day's primary overtime row, for callers that still want just one.

    Prefers the called-in row, then any overtime row. Kept because
    app/core/schedule/__init__.py exports it and api_v1.py imports it.
    """
    rows = [r for r in get_overtime_rows_for_date(session, user_id, date) if r.kind == "ot"]
    return next((r for r in rows if r.side == "full"), rows[0] if rows else None)


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


def build_ot_details(ot_shift, hourly_rate: float) -> dict:
    """Builds detailed info for an overtime shift.

    Recalculates pay based on the provided hourly_rate instead of using stored value.
    """
    return {
        "start_time": str(ot_shift.start_time),
        "end_time": str(ot_shift.end_time),
        "hours": ot_shift.hours,
        "pay": hourly_rate * ot_shift.hours,
        "hourly_rate": hourly_rate,
        "is_extension": ot_shift.side != "full",
    }
