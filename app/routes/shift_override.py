# app/routes/shift_override.py
"""Routes for manual shift overrides (adding/removing a regular shift for a day)."""

from datetime import date as date_cls
from datetime import time as time_cls

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user
from app.core.helpers import apply_to_dates, edit_redirect_url, require_own_or_admin
from app.core.schedule import clear_schedule_cache
from app.database.database import ShiftOverride, User, get_db

router = APIRouter(prefix="/shift-override", tags=["shift_override"])

# ETC is the custom labelled block; it carries its own times on the row.
_ALLOWED_CODES = {"N1", "N2", "N3", "ETC"}


def upsert_shift_override(
    session, *, user_id: int, date, shift_code: str, start_time, end_time, label, created_by: int
) -> None:
    """Write one shift override, replacing whatever that date already had.

    Shared with /day-edit/bulk so the two paths cannot drift apart.
    """
    existing = session.query(ShiftOverride).filter(ShiftOverride.user_id == user_id, ShiftOverride.date == date).first()
    if existing:
        existing.shift_code = shift_code
        existing.start_time = start_time
        existing.end_time = end_time
        existing.label = label or None
        existing.created_by = created_by
        return
    session.add(
        ShiftOverride(
            user_id=user_id,
            date=date,
            shift_code=shift_code,
            start_time=start_time,
            end_time=end_time,
            label=label or None,
            created_by=created_by,
        )
    )


@router.post("/add")
async def add_shift_override(
    user_id: int = Form(...),
    dates: list[date_cls] = Form(...),
    shift_code: str = Form(...),
    start_time: time_cls | None = Form(None),
    end_time: time_cls | None = Form(None),
    label: str = Form(""),
    return_to: str = Form(""),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_own_or_admin(current_user, user_id, "Du kan bara lägga till manuella pass för dig själv")

    if shift_code not in _ALLOWED_CODES:
        raise HTTPException(status_code=400, detail="Ogiltigt skiftkod, använd N1/N2/N3/ETC")

    # A custom block with no clock times has no hours and would render as a blank
    # row, so it is rejected rather than stored.
    if shift_code == "ETC" and not (start_time and end_time):
        raise HTTPException(status_code=400, detail="Ett övrigt-pass kräver både starttid och sluttid")

    # Times and label belong to ETC alone; a plain code override clears them so a
    # row switched from ETC back to N2 does not keep stale times.
    if shift_code != "ETC":
        start_time, end_time, label = None, None, ""

    def _row_for(override_date):
        return (
            session.query(ShiftOverride)
            .filter(ShiftOverride.user_id == user_id, ShiftOverride.date == override_date)
            .first()
        )

    def conflicts(override_date):
        # An existing override is replaced, as it always was. Absence outranks a
        # shift change, so those dates are left alone instead.
        from app.database.database import Absence

        has_absence = session.query(Absence).filter(Absence.user_id == user_id, Absence.date == override_date).first()
        return "hade frånvaro" if has_absence else None

    def write(override_date):
        upsert_shift_override(
            session,
            user_id=user_id,
            date=override_date,
            shift_code=shift_code,
            start_time=start_time,
            end_time=end_time,
            label=label,
            created_by=current_user.id,
        )

    written, skipped = apply_to_dates(dates, write, conflicts)

    session.commit()
    clear_schedule_cache()

    return RedirectResponse(
        url=edit_redirect_url(user_id, dates, return_to, written, skipped),
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
