"""Övertidsberäkningar och databashantering."""

import datetime

from app.core.constants import OT_RATE_DIVISOR


def calculate_overtime_pay(monthly_salary: int, hours: float) -> float:
    """
    Beräknar övertidsersättning.

    Formel: (månadslön / 72) * timmar

    Args:
        monthly_salary: Månadslön i SEK
        hours: Antal övertidstimmar

    Returns:
        Övertidsersättning i SEK
    """
    return (monthly_salary / OT_RATE_DIVISOR) * hours


def get_overtime_shift_for_date(session, user_id: int, date: datetime.date):
    """
    Hämtar övertidspass för en användare och datum.

    Returns:
        OvertimeShift eller None
    """
    if not session:
        return None

    from app.database.database import OvertimeShift

    return session.query(OvertimeShift).filter(OvertimeShift.user_id == user_id, OvertimeShift.date == date).first()


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
    """Bygger detaljerad info om ett övertidspass."""
    return {
        "start_time": str(ot_shift.start_time),
        "end_time": str(ot_shift.end_time),
        "hours": ot_shift.hours,
        "pay": ot_shift.ot_pay,
        "hourly_rate": hourly_rate,
    }
