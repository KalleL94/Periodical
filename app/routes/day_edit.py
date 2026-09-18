# app/routes/day_edit.py
"""Edits that span a calendar selection rather than a single day.

The per-row delete routes are keyed by row id, so they cannot express "remove the
extra-time row on these seven days". Without this, a multi-day edit could only be
undone by visiting every day page in turn.
"""

from datetime import date as date_cls
from datetime import time as time_cls

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user
from app.core.helpers import MAX_EDIT_DATES, apply_to_dates, edit_redirect_url, require_own_or_admin
from app.core.schedule import clear_schedule_cache
from app.database.database import (
    Absence,
    AbsenceType,
    OnCallOverride,
    OvertimeShift,
    ShiftOverride,
    User,
    get_db,
)

router = APIRouter(prefix="/day-edit", tags=["day_edit"])

# What the drawer can clear. The overtime entries use the same kind:side vocabulary
# the add form already posts, so the two stay in step.
_SINGLE_TARGETS = {
    "ot:before",
    "ot:after",
    "ot:full",
    "extra:before",
    "extra:after",
    "absence",
    "oncall",
    "shift",
}

# "all" wipes a day back to its rotation: every overtime and extra-time row, the
# absence, the on-call override and the shift change. One pass instead of picking
# each type in turn.
_TARGETS = _SINGLE_TARGETS | {"all"}

_MODELS = {"absence": Absence, "oncall": OnCallOverride, "shift": ShiftOverride}


def _queries_for(session: Session, user_id: int, what: str, date: date_cls) -> list:
    """The queries covering everything `what` names on one date."""
    if what == "all":
        return [
            session.query(OvertimeShift).filter(OvertimeShift.user_id == user_id, OvertimeShift.date == date),
            *(session.query(model).filter(model.user_id == user_id, model.date == date) for model in _MODELS.values()),
        ]
    if ":" in what:
        kind, side = what.split(":", 1)
        return [
            session.query(OvertimeShift).filter(
                OvertimeShift.user_id == user_id,
                OvertimeShift.date == date,
                OvertimeShift.kind == kind,
                OvertimeShift.side == side,
            )
        ]
    model = _MODELS[what]
    return [session.query(model).filter(model.user_id == user_id, model.date == date)]


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
        found = any(query.first() for query in _queries_for(session, user_id, what, date))
        return None if found else "hade inget att rensa"

    def write(date):
        for query in _queries_for(session, user_id, what, date):
            for row in query.all():
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


# The fields each area owns. The route reads only the posted area's: switching tab
# leaves the other two sets in the DOM and they still submit, so reading them
# would write values the user never looked at.
_AREAS = {
    "absence": ("absence_type", "absence_arrived", "absence_left"),
    "time": ("time_kind", "time_side", "time_start", "time_end", "time_hours"),
    "shift": ("shift_code", "shift_label", "shift_start", "shift_end"),
}


def _date_suffix(key: str, prefix: str) -> date_cls | None:
    """The date `<prefix><ISO date>` names, or None when it is some other field."""
    if not key.startswith(prefix):
        return None
    try:
        return date_cls.fromisoformat(key[len(prefix) :])
    except ValueError:
        return None


def _areas_by_date(form) -> dict[date_cls, str]:
    """Each row's own area. One post may mix them: Monday sick, Tuesday overtime."""
    areas: dict[date_cls, str] = {}
    for key, value in form.items():
        day = _date_suffix(key, "area_")
        if day is not None and (value or "").strip():
            areas[day] = value.strip()
    return areas


def _fields_for(form, day: date_cls, area: str) -> dict[str, str]:
    """One row's values, for its own area only.

    Every area's fields sit in every row, because switching a row's area only
    changes what is visible. Reading the others would write values the user never
    looked at.
    """
    fields = {}
    for field in _AREAS[area]:
        value = form.get(f"{field}_{day.isoformat()}")
        fields[field] = (value or "").strip()
    return fields


def _parse_time(value: str) -> time_cls | None:
    return time_cls.fromisoformat(value) if value else None


def _write_absence(session, user_id, day, fields, current_user):
    from app.routes.profile import upsert_absence

    raw = fields.get("absence_type", "")
    if not raw:
        return False
    try:
        absence_type = AbsenceType(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid absence type: {raw}") from None
    upsert_absence(
        session,
        user_id=user_id,
        date=day,
        absence_type=absence_type,
        left_at=fields.get("absence_left") or None,
        arrived_at=fields.get("absence_arrived") or None,
    )
    return True


def _write_time(session, user_id, day, fields, current_user):
    from app.core.rates import get_user_rates
    from app.core.schedule import calculate_overtime_pay, get_ot_hourly_rate_from_stored_wage, get_user_wage
    from app.routes.overtime import _KINDS, _SIDES, upsert_overtime

    kind = fields.get("time_kind", "")
    if not kind:
        return False
    side = fields.get("time_side") or "full"
    if kind not in _KINDS or side not in _SIDES:
        raise HTTPException(status_code=400, detail="Invalid kind or side")

    start = _parse_time(fields.get("time_start", ""))
    end = _parse_time(fields.get("time_end", ""))
    if start is None or end is None:
        raise HTTPException(status_code=400, detail="A time row needs both a start and an end")
    hours = float(fields.get("time_hours") or 0)

    # Priced exactly as /overtime/add does: the user's OT rate first, else the
    # rate derived from the stored wage. Extra time is worked time, not overtime.
    ot_pay = 0.0
    if kind == "ot":
        user = session.query(User).filter(User.id == user_id).first()
        raw_wage = get_user_wage(session, user_id, effective_date=day)
        rates = get_user_rates(user, session=session, effective_date=day) if user else {}
        rate = (
            rates["ot"]
            if rates.get("ot") is not None
            else get_ot_hourly_rate_from_stored_wage(session, user_id, raw_wage)
        )
        ot_pay = calculate_overtime_pay(raw_wage, hours, ot_hourly_rate=rate)

    upsert_overtime(
        session,
        user_id=user_id,
        date=day,
        start_time=start,
        end_time=end,
        hours=hours,
        kind=kind,
        side=side,
        ot_pay=ot_pay,
        created_by=current_user.id,
    )
    return True


def _write_shift(session, user_id, day, fields, current_user):
    from app.routes.shift_override import _ALLOWED_CODES, upsert_shift_override

    code = fields.get("shift_code", "")
    if not code:
        return False
    if code not in _ALLOWED_CODES:
        raise HTTPException(status_code=400, detail=f"Invalid shift code: {code}")

    start = _parse_time(fields.get("shift_start", ""))
    end = _parse_time(fields.get("shift_end", ""))
    if code == "ETC" and not (start and end):
        raise HTTPException(status_code=400, detail="A custom block needs both a start and an end")
    # Any code may carry its own window ("N1 but until 16:30"), so the times are
    # kept. Only one of the two is useless, since a window needs both ends.
    if not (start and end):
        start, end = None, None

    upsert_shift_override(
        session,
        user_id=user_id,
        date=day,
        shift_code=code,
        start_time=start,
        end_time=end,
        label=fields.get("shift_label", "") if code == "ETC" else "",
        created_by=current_user.id,
    )
    return True


_WRITERS = {"absence": _write_absence, "time": _write_time, "shift": _write_shift}


@router.post("/bulk")
async def bulk_edit(
    request: Request,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Write a different value per day, in one post.

    The form is read raw because each field name carries its own date. Only the
    posted area is acted on, and a row left blank writes nothing.
    """
    form = await request.form()

    user_id = int(form.get("user_id") or 0)
    require_own_or_admin(current_user, user_id, "Not authorized to edit days for other users")

    areas = _areas_by_date(form)
    unknown = sorted(set(areas.values()) - set(_AREAS))
    if unknown:
        raise HTTPException(status_code=400, detail=f"Invalid area {unknown[0]}, use one of {sorted(_AREAS)}")
    if len(areas) > MAX_EDIT_DATES:
        raise HTTPException(status_code=400, detail=f"Too many dates, the limit is {MAX_EDIT_DATES}")

    written = []
    for day in sorted(areas):
        area = areas[day]
        fields = _fields_for(form, day, area)
        if not any(fields.values()):
            continue
        if _WRITERS[area](session, user_id, day, fields, current_user):
            written.append(day)

    session.commit()
    clear_schedule_cache()

    dates = sorted(areas) or [date_cls.today()]
    return RedirectResponse(
        url=edit_redirect_url(user_id, dates, form.get("return_to") or "", written, []),
        status_code=303,
    )
