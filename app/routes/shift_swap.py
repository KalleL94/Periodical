# app/routes/shift_swap.py
"""Shift swap management routes - propose, accept, reject, cancel swaps."""

import json
from datetime import date as date_cls
from datetime import time as time_cls
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user, get_current_user_optional
from app.core.helpers import contrast_color
from app.core.schedule import calculate_shift_hours, clear_schedule_cache, get_shift_types
from app.core.swaps import (
    MAX_PAIRS,
    _can_swap,
    assignment,
    check_swap,
    effective_code,
    proposal_warnings,
    validate_proposal,
)
from app.core.utils import get_today
from app.database.database import (
    OnCallOverride,
    OnCallOverrideType,
    ShiftOverride,
    ShiftSwap,
    SwapStatus,
    User,
    UserRole,
    get_db,
    utcnow,
)
from app.routes.shared import render

router = APIRouter(prefix="/swaps", tags=["shift_swaps"])

MAX_WEEKS = 6


def _swap_rows(db: Session, swap: ShiftSwap, cache: dict) -> dict:
    """{(user_id, date): assignment} each slot gets, read before anything is written.

    On each date the two people exchange their effective days (OC included). A same-day
    swap collapses to two slots.
    """
    requester, target = swap.requester, swap.target
    rd, td = swap.requester_date, swap.target_date
    return {
        (requester.id, rd): assignment(db, target, rd, cache),
        (target.id, rd): assignment(db, requester, rd, cache),
        (target.id, td): assignment(db, requester, td, cache),
        (requester.id, td): assignment(db, target, td, cache),  # same day: overwrites the first key
    }


def _apply_swap_as_overrides(db: Session, swap: ShiftSwap, rows: dict) -> None:
    """Write the swap's rows from _swap_rows: a ShiftOverride per slot, plus an on-call window.

    "OFF" is written as a code so a shift given away stays free. A custom shift window
    goes on the ShiftOverride, an on-call window on an OnCallOverride ADD owned by the swap.
    """
    swap.replaced_rows = json.dumps(_take_manual_rows(db, rows))
    for (user_id, day), (code, start, end, oc_start, oc_end) in rows.items():
        db.add(
            ShiftOverride(
                user_id=user_id,
                date=day,
                shift_code=code,
                start_time=time_cls.fromisoformat(start) if start else None,
                end_time=time_cls.fromisoformat(end) if end else None,
                swap_id=swap.id,
                created_by=swap.target_id,
            )
        )
        if oc_start or oc_end:
            db.add(
                OnCallOverride(
                    user_id=user_id,
                    date=day,
                    override_type=OnCallOverrideType.ADD,
                    start_time=oc_start,
                    end_time=oc_end,
                    swap_id=swap.id,
                    created_by=swap.target_id,
                )
            )


def _proposed(swap: ShiftSwap) -> list:
    """[requester's day, target's day] as captured at propose, see assignment.

    A proposal from before the capture existed only knows the codes, so default times
    are assumed and a day with its own times reads as changed.
    """
    if swap.proposed_assignments:
        return json.loads(swap.proposed_assignments)
    return [[swap.requester_shift_code, None, None, None, None], [swap.target_shift_code, None, None, None, None]]


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _take_manual_rows(db: Session, slots) -> list[dict]:
    """Snapshot and delete the ShiftOverride and OnCallOverride rows on slots, so cancel can restore them.

    An OnCallOverride left in place would run after the swap's row and undo it.
    """
    snapshot = []
    for user_id, day in slots:
        base = {"user_id": user_id, "date": day.isoformat()}
        for row in db.query(ShiftOverride).filter(ShiftOverride.user_id == user_id, ShiftOverride.date == day):
            snapshot.append(
                {
                    "kind": "shift",
                    **base,
                    "shift_code": row.shift_code,
                    "start_time": _iso(row.start_time),
                    "end_time": _iso(row.end_time),
                    "label": row.label,
                    "created_by": row.created_by,
                }
            )
            db.delete(row)
        for row in db.query(OnCallOverride).filter(OnCallOverride.user_id == user_id, OnCallOverride.date == day):
            snapshot.append(
                {
                    "kind": "oncall",
                    **base,
                    "override_type": row.override_type.value,
                    "start_time": row.start_time,
                    "end_time": row.end_time,
                    "reason": row.reason,
                    "created_by": row.created_by,
                }
            )
            db.delete(row)
    return snapshot


def _restore_manual_rows(db: Session, swap: ShiftSwap) -> None:
    """Re-insert the rows accepting replaced, except on a slot that has a row of that kind again."""
    for fields in json.loads(swap.replaced_rows or "[]"):
        model = ShiftOverride if fields.pop("kind") == "shift" else OnCallOverride
        fields["date"] = date_cls.fromisoformat(fields["date"])
        taken = db.query(model.id).filter(model.user_id == fields["user_id"], model.date == fields["date"]).first()
        if taken:
            continue  # an admin changed the slot after the swap, the newer row wins
        if model is ShiftOverride:
            for key in ("start_time", "end_time"):
                fields[key] = time_cls.fromisoformat(fields[key]) if fields[key] else None
        else:
            fields["override_type"] = OnCallOverrideType(fields["override_type"])
        db.add(model(**fields))


def _kind(req_date: date_cls, tgt_date: date_cls, req_code: str, tgt_code: str) -> str | None:
    """What a pair does: cross-day, same-day mutual, or a one-way give away or take over."""
    if req_date != tgt_date:
        return "cross"
    if req_code != "OFF" and tgt_code != "OFF":
        return "mutual"
    if req_code != "OFF":
        return "give_away"
    if tgt_code != "OFF":
        return "take_over"
    return None


def _assess(db: Session, requester: User, target: User, pairs: list[tuple[date_cls, date_cls]], cache: dict) -> list:
    """check_swap verdict, rest warnings and kind per pair, as the compare screen shows them.

    Warnings are measured with every allowed pair applied together, blocked pairs get none.
    """
    results = []
    for req_date, tgt_date in pairs:
        req_code = effective_code(db, requester, req_date, cache)
        tgt_code = effective_code(db, target, tgt_date, cache)
        results.append(
            {
                "blocked": check_swap(db, requester, target, req_date, tgt_date, cache=cache),
                "warnings": [],
                "kind": _kind(req_date, tgt_date, req_code, tgt_code),
            }
        )
    allowed = [i for i, r in enumerate(results) if not r["blocked"]]
    warnings = proposal_warnings(db, requester, target, [pairs[i] for i in allowed], cache)
    for i, w in zip(allowed, warnings, strict=True):
        results[i]["warnings"] = w
    return results


class CheckPair(BaseModel):
    req_date: date_cls
    tgt_date: date_cls


class CheckRequest(BaseModel):
    target_id: int
    pairs: list[CheckPair] = Field(min_length=1, max_length=MAX_PAIRS)


def _colleagues(db: Session, user: User) -> list[User]:
    """Active non-admin users other than user, the people a swap can be proposed to."""
    return (
        db.query(User)
        .filter(User.is_active == 1, User.id != user.id, User.role != UserRole.ADMIN)
        .order_by(User.name)
        .all()
    )


MAX_START_DAYS = 400
MAX_MESSAGE = 255  # matches ShiftSwap.message String(255)


@router.post("/api/check")
async def check_pairs(
    body: CheckRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Blocked reason and kind per pair, rest warnings over all allowed pairs together."""
    target = db.get(User, body.target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Användaren hittades inte")
    pairs = [(p.req_date, p.tgt_date) for p in body.pairs]
    return {"results": _assess(db, current_user, target, pairs, {})}


def _effective_days(db: Session, user: User, start: date_cls, end: date_cls) -> dict:
    """{date: {"code", "start", "end"}} as a swap would hand the day over, see assignment."""
    days, cache = {}, {}
    for n in range((end - start).days + 1):
        d = start + timedelta(days=n)
        code, custom_start, custom_end, _, _ = assignment(db, user, d, cache)
        _, start_dt, end_dt = calculate_shift_hours(d, code)
        days[d] = {
            "code": code,
            "start": custom_start or (start_dt.strftime("%H:%M") if start_dt else None),
            "end": custom_end or (end_dt.strftime("%H:%M") if end_dt else None),
        }
    return days


@router.get("/api/compare")
async def compare_days(
    target_id: int,
    start: date_cls | None = None,
    weeks: int = 5,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Both users' effective days side by side in 1 to 6 whole Mon to Sun weeks, from this week at the earliest."""
    target = db.get(User, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Användaren hittades inte")
    if not _can_swap(target):
        raise HTTPException(status_code=404, detail="Användaren hittades inte")
    if not _can_swap(current_user):
        raise HTTPException(status_code=400, detail="Du kan inte byta pass")
    if start and start > get_today() + timedelta(days=MAX_START_DAYS):
        raise HTTPException(status_code=400, detail="Ogiltigt startdatum")
    today = get_today()
    first = max(start or today, today)
    first -= timedelta(days=first.weekday())
    last = first + timedelta(days=7 * min(max(weeks, 1), MAX_WEEKS) - 1)
    mine = _effective_days(db, current_user, first, last)
    theirs = _effective_days(db, target, first, last)
    days = []
    for n in range((last - first).days + 1):
        d = first + timedelta(days=n)
        days.append({"date": d.isoformat(), "me": mine[d], "them": theirs[d], "past": d <= today})
    return {"days": days}


@router.get("/api/day-takers")
async def day_takers(
    date: date_cls,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every colleague and what they could do with the viewer's shift on date."""
    result = []
    cache: dict = {}
    for colleague in _colleagues(db, current_user):
        [verdict] = _assess(db, current_user, colleague, [(date, date)], cache)
        if verdict["kind"] not in ("mutual", "give_away"):
            verdict["kind"] = None
        result.append({"id": colleague.id, "name": colleague.name, **verdict})
    return {"colleagues": result}


@router.get("/new", response_class=HTMLResponse)
async def new_swap_page(
    request: Request,
    date: date_cls | None = None,
    with_id: int | None = Query(None, alias="with"),
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Compare screen: pick a colleague, tap your day then theirs, send the pairs as one proposal."""
    if current_user is None:
        return RedirectResponse(url="/login?next=/swaps/new", status_code=302)
    colors = {s.code: [s.color, contrast_color(s.color)] for s in get_shift_types() if s.color and s.code != "OFF"}
    return render(
        "swap_new.html",
        {
            "request": request,
            "user": current_user,
            "colleagues": _colleagues(db, current_user),
            "date": date,
            "with_id": with_id,
            "shift_colors": colors,
            "max_pairs": MAX_PAIRS,
        },
    )


def _side(code: str | None, day: date_cls, free: bool) -> dict | None:
    """One side of a pair for the inbox; None when that side is a free day (one-way swap)."""
    return None if free else {"code": code or "?", "date": day}


def _inbox_groups(db: Session, rows: list[ShiftSwap], viewer: User) -> list[dict]:
    """One entry per proposal (a legacy row is a group of one), newest first, pairs from the viewer's side."""
    groups: dict[str, list[ShiftSwap]] = {}
    for row in rows:
        groups.setdefault(row.group_id or f"row-{row.id}", []).append(row)
    result = []
    for members in groups.values():
        members.sort(key=lambda r: r.id)
        first = members[0]
        pending = first.status == SwapStatus.PENDING
        mine_is_requester = first.requester_id == viewer.id
        # Rest is measured with the whole proposal applied, as the compare screen did.
        warnings = (
            proposal_warnings(db, first.requester, first.target, [(r.requester_date, r.target_date) for r in members])
            if pending
            else [[] for _ in members]
        )
        pairs = []
        for r, pair_warnings in zip(members, warnings, strict=True):
            same_day = r.requester_date == r.target_date
            req_free = r.requester_shift_code == "OFF" and same_day
            tgt_free = r.target_shift_code == "OFF" and same_day
            req_side = _side(r.requester_shift_code, r.requester_date, req_free)
            tgt_side = _side(r.target_shift_code, r.target_date, tgt_free)
            pairs.append(
                {
                    "give": req_side if mine_is_requester else tgt_side,
                    "take": tgt_side if mine_is_requester else req_side,
                    "warnings": pair_warnings,
                }
            )
        result.append(
            {
                "id": first.id,
                "status": first.status,
                "message": first.message,
                "requester": first.requester,
                "target": first.target,
                "other": first.target if mine_is_requester else first.requester,
                "pairs": pairs,
            }
        )
    return result


@router.get("/", response_class=HTMLResponse)
async def list_swaps(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all swaps for the current user (both sent and received), one card per proposal."""
    sent = (
        db.query(ShiftSwap)
        .filter(ShiftSwap.requester_id == current_user.id)
        .order_by(ShiftSwap.created_at.desc(), ShiftSwap.id.desc())
        .all()
    )
    received = (
        db.query(ShiftSwap)
        .filter(ShiftSwap.target_id == current_user.id)
        .order_by(ShiftSwap.created_at.desc(), ShiftSwap.id.desc())
        .all()
    )
    received_groups = _inbox_groups(db, received, current_user)
    pending_count = sum(1 for g in received_groups if g["status"] == SwapStatus.PENDING)

    return render(
        "shift_swaps.html",
        {
            "request": request,
            "user": current_user,
            "sent_groups": _inbox_groups(db, sent, current_user),
            "received_groups": received_groups,
            "pending_count": pending_count,
        },
    )


def _load_group(db: Session, swap_id: int) -> list[ShiftSwap]:
    """All rows of the proposal swap_id belongs to, oldest first; a legacy row is a group of one."""
    swap = db.get(ShiftSwap, swap_id)
    if not swap:
        raise HTTPException(status_code=404, detail="Bytet hittades inte")
    if swap.group_id is None:
        return [swap]
    return db.query(ShiftSwap).filter(ShiftSwap.group_id == swap.group_id).order_by(ShiftSwap.id).all()


def _load_pending_group_as_target(db: Session, swap_id: int, current_user: User) -> list[ShiftSwap]:
    group = _load_group(db, swap_id)
    if group[0].target_id != current_user.id:
        raise HTTPException(status_code=403, detail="Inte behörig")
    if any(s.status != SwapStatus.PENDING for s in group):
        raise HTTPException(status_code=400, detail="Bytet är inte längre väntande")
    return group


@router.post("/propose")
async def propose_swap(
    target_id: int = Form(...),
    req_date: list[date_cls] = Form(...),
    tgt_date: list[date_cls] = Form(...),
    message: str = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Propose one or more swaps to one colleague: pair i gives req_date[i] and takes tgt_date[i]."""
    target_user = db.get(User, target_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="Användaren hittades inte")
    if message and len(message) > MAX_MESSAGE:
        raise HTTPException(status_code=400, detail=f"Meddelandet är för långt (högst {MAX_MESSAGE} tecken)")
    if len(req_date) != len(tgt_date):
        raise HTTPException(status_code=400, detail="Ogiltigt förslag")

    pairs = list(zip(req_date, tgt_date, strict=True))
    cache: dict = {}
    reason = validate_proposal(db, current_user, target_user, pairs, cache)
    if reason:
        raise HTTPException(status_code=400, detail=reason)

    group_id = uuid4().hex
    for rd, td in pairs:
        given = assignment(db, current_user, rd, cache)
        taken = assignment(db, target_user, td, cache)
        db.add(
            ShiftSwap(
                requester_id=current_user.id,
                target_id=target_id,
                requester_date=rd,
                target_date=td,
                requester_shift_code=given[0],
                target_shift_code=taken[0],
                proposed_assignments=json.dumps([given, taken]),
                message=message,
                applied_as_override=True,
                group_id=group_id,
            )
        )
    db.commit()

    return RedirectResponse(url="/swaps", status_code=303)


@router.post("/{swap_id}/accept")
async def accept_swap(
    swap_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Accept a whole proposal (target user only): every pair is applied, or none."""
    group = _load_pending_group_as_target(db, swap_id, current_user)
    group_ids = frozenset(s.id for s in group)

    # Check every pair before writing anything, so a failure leaves the group PENDING.
    planned = []
    cache: dict = {}
    for swap in group:
        reason = check_swap(
            db,
            swap.requester,
            swap.target,
            swap.requester_date,
            swap.target_date,
            exclude_swap_ids=group_ids,
            cache=cache,
        )
        if reason:
            raise HTTPException(status_code=400, detail=reason)
        rows = _swap_rows(db, swap, cache)
        # The whole day is compared, custom times and the on-call window included.
        given = rows[(swap.target_id, swap.requester_date)]
        taken = rows[(swap.requester_id, swap.target_date)]
        if _proposed(swap) != [list(given), list(taken)]:
            raise HTTPException(status_code=400, detail="Schemat har ändrats sedan förslaget")
        planned.append(rows)

    now = utcnow()
    for swap, rows in zip(group, planned, strict=True):
        # Rows were read before any write, so earlier pairs cannot leak in.
        _apply_swap_as_overrides(db, swap, rows)
        # A swap proposed before overrides existed must leave swap_map too, or it counts twice.
        swap.applied_as_override = True
        swap.status = SwapStatus.ACCEPTED
        swap.responded_at = now
    db.commit()
    clear_schedule_cache()

    return RedirectResponse(url="/swaps", status_code=303)


@router.post("/{swap_id}/reject")
async def reject_swap(
    swap_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reject a whole proposal (target user only)."""
    now = utcnow()
    for swap in _load_pending_group_as_target(db, swap_id, current_user):
        swap.status = SwapStatus.REJECTED
        swap.responded_at = now
    db.commit()

    return RedirectResponse(url="/swaps", status_code=303)


@router.post("/{swap_id}/cancel")
async def cancel_swap(
    swap_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel a whole pending or accepted proposal (requester, target, or admin)."""
    group = _load_group(db, swap_id)
    first = group[0]
    is_participant = current_user.id in (first.requester_id, first.target_id)
    is_admin = current_user.role.value == "admin"

    if not is_participant and not is_admin:
        raise HTTPException(status_code=403, detail="Inte behörig")
    if any(s.status not in (SwapStatus.PENDING, SwapStatus.ACCEPTED) for s in group):
        raise HTTPException(status_code=400, detail="Kan bara avbryta väntande eller accepterade byten")

    # Legacy swaps own no rows; rows an admin has since edited have swap_id cleared.
    swap_ids = [s.id for s in group]
    db.query(ShiftOverride).filter(ShiftOverride.swap_id.in_(swap_ids)).delete()
    db.query(OnCallOverride).filter(OnCallOverride.swap_id.in_(swap_ids)).delete()
    now = utcnow()
    for swap in group:
        _restore_manual_rows(db, swap)
        swap.status = SwapStatus.CANCELLED
        swap.responded_at = now
    db.commit()
    clear_schedule_cache()

    return RedirectResponse(url="/swaps", status_code=303)
