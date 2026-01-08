# app/routes/overtime.py
"""
Overtime shift management routes - add and delete overtime shifts.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user
from app.core.schedule import calculate_overtime_pay, clear_schedule_cache, get_user_wage
from app.database.database import OvertimeShift, User, UserRole, get_db

router = APIRouter(prefix="/overtime", tags=["overtime"])


@router.post("/add")
async def add_overtime_shift(
    user_id: int = Form(...),
    date: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    hours: float = Form(8.5),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Add an overtime shift.

    Permissions:
    - Admin: can add for any user
    - User: can only add for themselves
    """
    # Permission check
    if current_user.role != UserRole.ADMIN and user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to add overtime for other users")

    # Parse date first (needed for wage lookup)
    ot_date = datetime.strptime(date, "%Y-%m-%d").date()

    # Get user's wage for the specific date (temporal query)
    monthly_salary = get_user_wage(session, user_id, effective_date=ot_date)

    # Calculate OT pay
    ot_pay = calculate_overtime_pay(monthly_salary, hours)

    # Parse times
    start_t = datetime.strptime(start_time, "%H:%M").time()
    end_t = datetime.strptime(end_time, "%H:%M").time()

    # Create overtime shift record
    ot_shift = OvertimeShift(
        user_id=user_id,
        date=ot_date,
        start_time=start_t,
        end_time=end_t,
        hours=hours,
        ot_pay=ot_pay,
        created_by=current_user.id,
    )

    session.add(ot_shift)
    session.commit()

    # Clear schedule cache to reflect changes
    clear_schedule_cache()

    return RedirectResponse(url=f"/day/{user_id}/{ot_date.year}/{ot_date.month}/{ot_date.day}", status_code=303)


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

    # Permission check
    if current_user.role != UserRole.ADMIN and ot_shift.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this overtime shift")

    # Save info for redirect
    user_id = ot_shift.user_id
    date = ot_shift.date

    # Delete
    session.delete(ot_shift)
    session.commit()

    # Clear schedule cache to reflect changes
    clear_schedule_cache()

    return RedirectResponse(url=f"/day/{user_id}/{date.year}/{date.month}/{date.day}", status_code=303)
