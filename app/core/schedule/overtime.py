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
    """Builds detailed info for an overtime shift.

    Recalculates pay based on the provided hourly_rate instead of using stored value.
    """
    return {
        "start_time": str(ot_shift.start_time),
        "end_time": str(ot_shift.end_time),
        "hours": ot_shift.hours,
        "pay": hourly_rate * ot_shift.hours,
        "hourly_rate": hourly_rate,
        "is_extension": ot_shift.is_extension,
    }


def compute_ot_details(
    session,
    user_id: int,
    date: datetime.date,
    monthly_salary: float,
    ot_rate: float | None = None,
    absence=None,
) -> dict:
    """Calculates overtime details for a date.

    Fetches the overtime shift for the day and the previous day (for on-call calculation),
    computes pay and returns aggregated info.

    Returns:
        Dict with ot_shift, ot_shift_for_oncall, ot_pay, ot_details.
        ot_shift and ot_shift_for_oncall are None if no overtime shift exists.
    """
    from app.core.time_utils import parse_ot_times

    ot_shift = get_overtime_shift_for_date(session, user_id, date)

    # Also check previous day for OT that crosses midnight (affects on-call pay)
    ot_shift_for_oncall = ot_shift
    if not ot_shift_for_oncall:
        prev_day = date - datetime.timedelta(days=1)
        prev_ot = get_overtime_shift_for_date(session, user_id, prev_day)
        if prev_ot:
            try:
                _, ot_end_dt = parse_ot_times(prev_ot, prev_day)
                if ot_end_dt.date() > prev_day:
                    ot_shift_for_oncall = prev_ot
            except ValueError:
                pass

    ot_pay = 0.0
    ot_details: dict = {}

    if ot_shift and not absence:
        from .wages import get_ot_hourly_rate_from_stored_wage, get_user_wage

        _raw_wage = get_user_wage(session, user_id, monthly_salary, effective_date=date)
        hourly_rate = (
            ot_rate if ot_rate is not None else get_ot_hourly_rate_from_stored_wage(session, user_id, _raw_wage)
        )
        ot_pay = hourly_rate * ot_shift.hours
        ot_details = build_ot_details(ot_shift, hourly_rate)

    return {
        "ot_shift": ot_shift,
        "ot_shift_for_oncall": ot_shift_for_oncall,
        "ot_pay": ot_pay,
        "ot_details": ot_details,
    }
