# app/routes/day_pay_override.py
"""Routes for manual OB and on-call hour overrides on a specific day."""

from datetime import date as date_cls

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user
from app.core.helpers import require_own_or_admin
from app.core.schedule import clear_schedule_cache
from app.database.database import DayPayOverride, User, get_db

router = APIRouter(prefix="/day-pay-override", tags=["day_pay_override"])


@router.post("/set")
async def set_day_pay_override(
    request: Request,
    user_id: int = Form(...),
    override_date: date_cls = Form(..., alias="date"),
    reason: str = Form(default=""),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # The request is still read raw below because ob_hours_*/oc_hours_* keys are dynamic.
    form = await request.form()
    reason_text = reason.strip() or None

    require_own_or_admin(current_user, user_id, "Du kan bara ange overrides for dig sjalv")

    # Collect ob_hours_* and oc_hours_* fields from the form
    ob_hours: dict[str, float] = {}
    oncall_hours: dict[str, float] = {}
    for key, val in form.items():
        val_str = str(val).strip()
        if not val_str:
            continue
        if key.startswith("ob_hours_"):
            code = key[len("ob_hours_") :]
            try:
                ob_hours[code] = float(val_str)
            except ValueError:
                pass
        elif key.startswith("oc_hours_"):
            code = key[len("oc_hours_") :]
            try:
                oncall_hours[code] = float(val_str)
            except ValueError:
                pass

    ob_json = ob_hours if ob_hours else None
    oncall_json = oncall_hours if oncall_hours else None

    if ob_json is None and oncall_json is None:
        raise HTTPException(status_code=400, detail="Ange minst ett timvarde att overrida")

    existing = (
        session.query(DayPayOverride)
        .filter(DayPayOverride.user_id == user_id, DayPayOverride.date == override_date)
        .first()
    )
    if existing:
        existing.ob_hours_override = ob_json
        existing.oncall_hours_override = oncall_json
        existing.reason = reason_text
        existing.created_by = current_user.id
    else:
        session.add(
            DayPayOverride(
                user_id=user_id,
                date=override_date,
                ob_hours_override=ob_json,
                oncall_hours_override=oncall_json,
                reason=reason_text,
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
async def delete_day_pay_override(
    override_id: int,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    override = session.query(DayPayOverride).filter(DayPayOverride.id == override_id).first()
    if not override:
        raise HTTPException(status_code=404, detail="Override hittades inte")

    require_own_or_admin(current_user, override.user_id, "Du kan bara ta bort dina egna overrides")

    redirect_date = override.date
    redirect_user = override.user_id

    session.delete(override)
    session.commit()
    clear_schedule_cache()

    return RedirectResponse(
        url=f"/day/{redirect_user}/{redirect_date.year}/{redirect_date.month}/{redirect_date.day}",
        status_code=303,
    )
