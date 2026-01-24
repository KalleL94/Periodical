# app/routes/oncall.py
"""
On-call override management routes - add and remove on-call shifts.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user
from app.core.schedule import clear_schedule_cache
from app.database.database import OnCallOverride, OnCallOverrideType, User, UserRole, get_db

router = APIRouter(prefix="/oncall", tags=["oncall"])


@router.post("/add")
async def add_oncall_override(
    user_id: int = Form(...),
    date: str = Form(...),
    reason: str = Form(None),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Add an on-call shift for a person who doesn't normally have one.

    Permissions:
    - Admin: can add for any user
    - User: can only add for themselves
    """
    # Permission check
    if current_user.role != UserRole.ADMIN and user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to add on-call for other users")

    # Parse date
    oc_date = datetime.strptime(date, "%Y-%m-%d").date()

    # Check if override already exists for this date
    existing = (
        session.query(OnCallOverride).filter(OnCallOverride.user_id == user_id, OnCallOverride.date == oc_date).first()
    )

    if existing:
        # Update existing override
        existing.override_type = OnCallOverrideType.ADD
        existing.reason = reason
        existing.created_by = current_user.id
    else:
        # Create new override
        override = OnCallOverride(
            user_id=user_id,
            date=oc_date,
            override_type=OnCallOverrideType.ADD,
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
    date: str = Form(...),
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
    if current_user.role != UserRole.ADMIN and user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to remove on-call for other users")

    # Parse date
    oc_date = datetime.strptime(date, "%Y-%m-%d").date()

    # Check if override already exists for this date
    existing = (
        session.query(OnCallOverride).filter(OnCallOverride.user_id == user_id, OnCallOverride.date == oc_date).first()
    )

    if existing:
        # Update existing override
        existing.override_type = OnCallOverrideType.REMOVE
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
    if current_user.role != UserRole.ADMIN and override.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this on-call override")

    # Save info for redirect
    user_id = override.user_id
    date = override.date

    # Delete
    session.delete(override)
    session.commit()

    # Clear schedule cache to reflect changes
    clear_schedule_cache()

    return RedirectResponse(url=f"/day/{user_id}/{date.year}/{date.month}/{date.day}", status_code=303)
