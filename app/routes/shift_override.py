# app/routes/shift_override.py
"""Routes for manual shift overrides (adding/removing a regular shift for a day)."""

import json
from datetime import date as date_cls
from datetime import time as time_cls

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user
from app.core.helpers import apply_to_dates, edit_redirect_url, require_own_or_admin
from app.core.schedule import clear_schedule_cache
from app.core.translations import tr
from app.database.database import OnCallOverride, ShiftOverride, ShiftSwap, User, UserRole, get_db

router = APIRouter(prefix="/shift-override", tags=["shift_override"])

# ETC is the custom labelled block; it carries its own times on the row.
_ALLOWED_CODES = {"N1", "N2", "N3", "ETC"}


def refuse_swap_rows(session, current_user: User, user_id: int, dates) -> None:
    """Raise 400 if a non-admin would change a slot that holds a row an accepted swap wrote.

    Covers ShiftOverride and OnCallOverride writes: an on-call change would run after
    the swap's row and undo it. A swap's own OnCallOverride (its on-call window) counts too.

    A participant undoes a swap by cancelling it, which removes every row at once.
    An admin may change one row; upsert_shift_override then makes it manual.
    """
    if current_user.role == UserRole.ADMIN or not dates:
        return
    owned = any(
        session.query(model.id)
        .filter(model.user_id == user_id, model.date.in_(list(dates)), model.swap_id.isnot(None))
        .first()
        for model in (ShiftOverride, OnCallOverride)
    )
    if owned:
        raise HTTPException(status_code=400, detail=tr("swap_row_protected", current_user.language))


def release_swap_row(session, row) -> None:
    """Make a swap's ShiftOverride or OnCallOverride row manual before an admin edits or deletes it.

    The owning swap also forgets the manual row of the same kind it replaced on that
    slot, so cancelling the swap later cannot bring the pre-swap row back over the
    admin's change. Each swap row of a group keeps its own snapshot, found by swap_id.
    """
    if getattr(row, "swap_id", None) is None:  # also a model with no swap_id, e.g. Absence
        return
    swap = session.get(ShiftSwap, row.swap_id)
    if swap is not None and swap.replaced_rows:
        slot = ("shift" if isinstance(row, ShiftOverride) else "oncall", row.user_id, row.date.isoformat())
        kept = [f for f in json.loads(swap.replaced_rows) if (f["kind"], f["user_id"], f["date"]) != slot]
        swap.replaced_rows = json.dumps(kept)
    row.swap_id = None


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
        # An edited swap row becomes a manual row, so cancelling the swap leaves it.
        release_swap_row(session, existing)
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
    refuse_swap_rows(session, current_user, user_id, dates)

    # A custom block with no clock times has no hours and would render as a blank
    # row, so it is rejected rather than stored.
    if shift_code == "ETC" and not (start_time and end_time):
        raise HTTPException(status_code=400, detail="Ett övrigt-pass kräver både starttid och sluttid")

    # The label belongs to ETC alone, so a row switched back to N2 keeps no stale
    # text. The times do not: an ordinary shift may carry its own window, which is
    # how "I worked N1 but stayed until 16:30" is recorded.
    if shift_code != "ETC":
        label = ""

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
    refuse_swap_rows(session, current_user, override.user_id, [override.date])

    redirect_date = override.date
    redirect_user = override.user_id

    release_swap_row(session, override)
    session.delete(override)
    session.commit()
    clear_schedule_cache()

    return RedirectResponse(
        url=f"/day/{redirect_user}/{redirect_date.year}/{redirect_date.month}/{redirect_date.day}",
        status_code=303,
    )
