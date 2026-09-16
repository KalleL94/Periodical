# app/routes/overtime.py
"""
Overtime shift management routes - add and delete overtime shifts.
"""

from datetime import date as date_cls
from datetime import time as time_cls

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user
from app.core.helpers import apply_to_dates, edit_redirect_url, require_own_or_admin
from app.core.schedule import (
    calculate_overtime_pay,
    clear_schedule_cache,
    get_ot_hourly_rate_from_stored_wage,
    get_user_wage,
)
from app.database.database import OvertimeShift, User, get_db

router = APIRouter(prefix="/overtime", tags=["overtime"])

_KINDS = {"ot", "extra"}
_SIDES = {"before", "after", "full"}


@router.post("/add")
async def add_overtime_shift(
    user_id: int = Form(...),
    dates: list[date_cls] = Form(...),
    start_time: time_cls = Form(...),
    end_time: time_cls = Form(...),
    hours: float = Form(8.5),
    kind: str = Form("ot"),
    side: str = Form("full"),
    return_to: str = Form(""),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Add an overtime shift.

    Permissions:
    - Admin: can add for any user
    - User: can only add for themselves
    """
    require_own_or_admin(current_user, user_id, "Not authorized to add overtime for other users")

    # These reach a unique index and a pay branch, so they are a trust boundary.
    if kind not in _KINDS:
        raise HTTPException(status_code=400, detail=f"Invalid kind, use one of {sorted(_KINDS)}")
    if side not in _SIDES:
        raise HTTPException(status_code=400, detail=f"Invalid side, use one of {sorted(_SIDES)}")

    from app.core.rates import get_user_rates

    ot_user = session.query(User).filter(User.id == user_id).first()

    def _rate_for(ot_date):
        """The OT hourly rate on one date; wages and rates are temporal."""
        raw_wage = get_user_wage(session, user_id, effective_date=ot_date)
        rates = get_user_rates(ot_user, session=session, effective_date=ot_date) if ot_user else {}
        if rates.get("ot") is not None:
            return rates["ot"]
        return get_ot_hourly_rate_from_stored_wage(session, user_id, raw_wage)

    # Extra time is worked time, not overtime: it earns OB through the day's segment
    # list and carries no OT pay, the same convention substitute rows already use.
    def _row_for(ot_date):
        return (
            session.query(OvertimeShift)
            .filter(
                OvertimeShift.user_id == user_id,
                OvertimeShift.date == ot_date,
                OvertimeShift.kind == kind,
                OvertimeShift.side == side,
            )
            .first()
        )

    def conflicts(ot_date):
        # Extra time is worked time, not overtime: it earns OB through the day's
        # segment list and carries no OT pay, the same convention substitutes use.
        return "hade redan en rad av den typen" if _row_for(ot_date) else None

    def write(ot_date):
        ot_pay = (
            calculate_overtime_pay(
                get_user_wage(session, user_id, effective_date=ot_date),
                hours,
                ot_hourly_rate=_rate_for(ot_date),
            )
            if kind == "ot"
            else 0.0
        )
        # One row per (user, date, kind, side). The unique indexes on the model
        # enforce the same thing, which is why the old duplicate deletion is gone.
        existing = _row_for(ot_date)
        if existing:
            existing.start_time = start_time
            existing.end_time = end_time
            existing.hours = hours
            existing.ot_pay = ot_pay
        else:
            session.add(
                OvertimeShift(
                    user_id=user_id,
                    date=ot_date,
                    start_time=start_time,
                    end_time=end_time,
                    hours=hours,
                    ot_pay=ot_pay,
                    kind=kind,
                    side=side,
                    created_by=current_user.id,
                )
            )

    written, skipped = apply_to_dates(dates, write, conflicts)

    session.commit()

    # Clear schedule cache to reflect changes. Once, after the whole loop.
    clear_schedule_cache()

    return RedirectResponse(
        url=edit_redirect_url(user_id, dates, return_to, written, skipped),
        status_code=303,
    )


@router.post("/{ot_id}/delete")
async def delete_overtime_shift(
    ot_id: int, session: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """
    Delete an overtime shift.

    Permissions:
    - Admin: can delete any OT shift
    - User: can only delete their own OT shifts
    """
    ot_shift = session.query(OvertimeShift).get(ot_id)

    if not ot_shift:
        raise HTTPException(status_code=404, detail="Overtime shift not found")

    require_own_or_admin(current_user, ot_shift.user_id, "Not authorized to delete this overtime shift")

    # Save info for redirect
    user_id = ot_shift.user_id
    date = ot_shift.date

    # Delete
    session.delete(ot_shift)
    session.commit()

    # Clear schedule cache to reflect changes
    clear_schedule_cache()

    return RedirectResponse(url=f"/day/{user_id}/{date.year}/{date.month}/{date.day}", status_code=303)
