# app/routes/day_edit.py
"""Edits that span a calendar selection rather than a single day.

The per-row delete routes are keyed by row id, so they cannot express "remove the
extra-time row on these seven days". Without this, a multi-day edit could only be
undone by visiting every day page in turn.
"""

from datetime import date as date_cls

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user
from app.core.helpers import apply_to_dates, edit_redirect_url, require_own_or_admin
from app.core.schedule import clear_schedule_cache
from app.database.database import (
    Absence,
    OnCallOverride,
    OvertimeShift,
    ShiftOverride,
    User,
    get_db,
)

router = APIRouter(prefix="/day-edit", tags=["day_edit"])

# What the drawer can clear. The overtime entries use the same kind:side vocabulary
# the add form already posts, so the two stay in step.
_TARGETS = {
    "ot:before",
    "ot:after",
    "ot:full",
    "extra:before",
    "extra:after",
    "absence",
    "oncall",
    "shift",
}


def _query_for(session: Session, user_id: int, what: str, date: date_cls):
    """The rows `what` names on one date, as a query."""
    if ":" in what:
        kind, side = what.split(":", 1)
        return session.query(OvertimeShift).filter(
            OvertimeShift.user_id == user_id,
            OvertimeShift.date == date,
            OvertimeShift.kind == kind,
            OvertimeShift.side == side,
        )
    model = {"absence": Absence, "oncall": OnCallOverride, "shift": ShiftOverride}[what]
    return session.query(model).filter(model.user_id == user_id, model.date == date)


@router.post("/clear")
async def clear_days(
    user_id: int = Form(...),
    dates: list[date_cls] = Form(...),
    what: str = Form(...),
    return_to: str = Form(""),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove one kind of change from every selected date.

    A date with nothing to remove is reported rather than treated as an error, so
    clearing a whole week says how much it actually found.
    """
    require_own_or_admin(current_user, user_id, "Not authorized to clear days for other users")

    if what not in _TARGETS:
        raise HTTPException(status_code=400, detail=f"Invalid target, use one of {sorted(_TARGETS)}")

    def conflicts(date):
        # "Nothing here" is not a failure, it is the report.
        return None if _query_for(session, user_id, what, date).first() else "hade inget att rensa"

    def write(date):
        for row in _query_for(session, user_id, what, date).all():
            session.delete(row)

    # apply_to_dates skips the conflict check for a single date, which would delete
    # nothing and report nothing. Asking directly keeps one date honest.
    if len(dates) == 1 and conflicts(dates[0]):
        written, skipped = [], [(dates[0], "hade inget att rensa")]
    else:
        written, skipped = apply_to_dates(dates, write, conflicts)

    session.commit()
    clear_schedule_cache()

    return RedirectResponse(
        url=edit_redirect_url(user_id, dates, return_to, written, skipped, verb="rensade"),
        status_code=303,
    )
