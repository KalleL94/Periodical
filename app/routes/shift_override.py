# app/routes/shift_override.py
"""Routes for manual shift overrides (adding/removing a regular shift for a day)."""

from datetime import date as date_cls

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user
from app.core.helpers import require_own_or_admin
from app.core.schedule import clear_schedule_cache
from app.database.database import ShiftOverride, User, get_db

router = APIRouter(prefix="/shift-override", tags=["shift_override"])

_ALLOWED_CODES = {"N1", "N2", "N3"}


@router.post("/add")
async def add_shift_override(
    user_id: int = Form(...),
    date: date_cls = Form(...),
    shift_code: str = Form(...),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_own_or_admin(current_user, user_id, "Du kan bara lägga till manuella pass för dig själv")

    if shift_code not in _ALLOWED_CODES:
        raise HTTPException(status_code=400, detail="Ogiltigt skiftkod, använd N1/N2/N3")

    override_date = date

    existing = (
        session.query(ShiftOverride)
        .filter(ShiftOverride.user_id == user_id, ShiftOverride.date == override_date)
        .first()
    )
    if existing:
        existing.shift_code = shift_code
        existing.created_by = current_user.id
    else:
        session.add(
            ShiftOverride(
                user_id=user_id,
                date=override_date,
                shift_code=shift_code,
                created_by=current_user.id,
            )
        )

    session.commit()
    clear_schedule_cache()

    return RedirectResponse(
        url=f"/day/{user_id}/{override_date.year}/{override_date.month}/{override_date.day}",
        status_code=303,
    )


@router.post("/{override_id}/delete")
async def delete_shift_override(
    override_id: int,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    override = session.query(ShiftOverride).filter(ShiftOverride.id == override_id).first()
    if not override:
        raise HTTPException(status_code=404, detail="Override hittades inte")

    require_own_or_admin(current_user, override.user_id, "Du kan bara ta bort dina egna manuella pass")

    redirect_date = override.date
    redirect_user = override.user_id

    session.delete(override)
    session.commit()
    clear_schedule_cache()

    return RedirectResponse(
        url=f"/day/{redirect_user}/{redirect_date.year}/{redirect_date.month}/{redirect_date.day}",
        status_code=303,
    )
