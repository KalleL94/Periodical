# app/routes/oncall.py
"""
On-call override management routes - add and remove on-call shifts.
"""

from datetime import date as date_cls
from datetime import time as time_cls

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user
from app.core.helpers import require_own_or_admin
from app.core.schedule import clear_schedule_cache
from app.database.database import OnCallOverride, OnCallOverrideType, User, get_db

router = APIRouter(prefix="/oncall", tags=["oncall"])


def _parse_window(start_time: str | None, end_time: str | None) -> tuple[str | None, str | None]:
    """Validate an optional "HH:MM" on-call window and normalise blanks to None.

    An end of "00:00" means midnight at the end of the day, so it is not compared
    against the start. Both empty means the whole day.
    """
    start = (start_time or "").strip() or None
    end = (end_time or "").strip() or None

    for value in (start, end):
        if value is None:
            continue
        try:
            time_cls.fromisoformat(value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid time: {value}") from None

    if start and end and end != "00:00" and end <= start:
        raise HTTPException(status_code=400, detail="On-call end time must be after the start time")

    return start, end


@router.post("/add")
async def add_oncall_override(
    user_id: int = Form(...),
    date: date_cls = Form(...),
    start_time: str = Form(None),
    end_time: str = Form(None),
    reason: str = Form(None),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Add an on-call shift for a person who doesn't normally have one.

    An optional start_time/end_time window ("HH:MM") limits the shift to part of
    the day, so two people can share one on-call day. Leave both blank for a full
    24-hour shift.

    Permissions:
    - Admin: can add for any user
    - User: can only add for themselves
    """
    require_own_or_admin(current_user, user_id, "Not authorized to add on-call for other users")

    oc_date = date
    window_start, window_end = _parse_window(start_time, end_time)

    # Check if override already exists for this date
    existing = (
        session.query(OnCallOverride).filter(OnCallOverride.user_id == user_id, OnCallOverride.date == oc_date).first()
    )

    if existing:
        # Update existing override
        existing.override_type = OnCallOverrideType.ADD
        existing.start_time = window_start
        existing.end_time = window_end
        existing.reason = reason
        existing.created_by = current_user.id
    else:
        # Create new override
        override = OnCallOverride(
            user_id=user_id,
            date=oc_date,
            override_type=OnCallOverrideType.ADD,
            start_time=window_start,
            end_time=window_end,
            reason=reason,
            created_by=current_user.id,
        )
        session.add(override)

    session.commit()

    # Clear schedule cache to reflect changes
    clear_schedule_cache()

    return RedirectResponse(url=f"/day/{user_id}/{oc_date.year}/{oc_date.month}/{oc_date.day}", status_code=303)


@router.post("/remove")
async def remove_oncall_override(
    user_id: int = Form(...),
    date: date_cls = Form(...),
    reason: str = Form(None),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Remove/cancel an on-call shift from the rotation.

    Permissions:
    - Admin: can remove for any user
    - User: can only remove for themselves
    """
    # Permission check
    require_own_or_admin(current_user, user_id, "Not authorized to remove on-call for other users")

    oc_date = date

    # Check if override already exists for this date
    existing = (
        session.query(OnCallOverride).filter(OnCallOverride.user_id == user_id, OnCallOverride.date == oc_date).first()
    )

    if existing:
        # Update existing override
        existing.override_type = OnCallOverrideType.REMOVE
        existing.start_time = None
        existing.end_time = None
        existing.reason = reason
        existing.created_by = current_user.id
    else:
        # Create new override
        override = OnCallOverride(
            user_id=user_id,
            date=oc_date,
            override_type=OnCallOverrideType.REMOVE,
            reason=reason,
            created_by=current_user.id,
        )
        session.add(override)

    session.commit()

    # Clear schedule cache to reflect changes
    clear_schedule_cache()

    return RedirectResponse(url=f"/day/{user_id}/{oc_date.year}/{oc_date.month}/{oc_date.day}", status_code=303)


@router.post("/{override_id}/delete")
async def delete_oncall_override(
    override_id: int,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete an on-call override (restore to rotation).

    Permissions:
    - Admin: can delete any override
    - User: can only delete their own overrides
    """
    override = session.query(OnCallOverride).get(override_id)

    if not override:
        raise HTTPException(status_code=404, detail="On-call override not found")

    # Permission check
    require_own_or_admin(current_user, override.user_id, "Not authorized to delete this on-call override")

    # Save info for redirect
    user_id = override.user_id
    date = override.date

    # Delete
    session.delete(override)
    session.commit()

    # Clear schedule cache to reflect changes
    clear_schedule_cache()

    return RedirectResponse(url=f"/day/{user_id}/{date.year}/{date.month}/{date.day}", status_code=303)
