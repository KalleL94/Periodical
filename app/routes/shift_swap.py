# app/routes/shift_swap.py
"""Shift swap management routes - propose, accept, reject, cancel swaps."""

from datetime import date as date_cls
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user, get_current_user_optional
from app.core.helpers import contrast_color
from app.core.schedule import build_week_data, calculate_shift_hours, clear_schedule_cache, get_shift_types
from app.core.swaps import MAX_PAIRS, _can_swap, check_swap, effective_code, swap_warnings, validate_proposal
from app.core.utils import get_today
from app.database.database import ShiftOverride, ShiftSwap, SwapStatus, User, UserRole, get_db, utcnow
from app.routes.shared import render

router = APIRouter(prefix="/swaps", tags=["shift_swaps"])

MAX_WEEKS = 6


def _apply_swap_as_overrides(db: Session, swap: ShiftSwap, req_code: str, tgt_code: str, cache: dict) -> None:
    """Write the swap as ShiftOverride rows, the same result the legacy swap_map gives.

    On each date the two people exchange their effective shifts (OC included). A same-day
    swap collapses to two rows. "OFF" is written as a code so a shift given away stays free.
    """
    requester, target = swap.requester, swap.target
    rd, td = swap.requester_date, swap.target_date
    rows = {
        (requester.id, rd): effective_code(db, target, rd, cache),
        (target.id, rd): req_code,
        (target.id, td): effective_code(db, requester, td, cache),
        (requester.id, td): tgt_code,  # same day: overwrites the first key
    }
    for (user_id, day), code in rows.items():
        db.add(ShiftOverride(user_id=user_id, date=day, shift_code=code, swap_id=swap.id, created_by=target.id))


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


def _assess(db: Session, requester: User, target: User, req_date: date_cls, tgt_date: date_cls, cache: dict) -> dict:
    """check_swap verdict, rest warnings and kind for one pair, as the compare screen shows it."""
    blocked = check_swap(db, requester, target, req_date, tgt_date, cache=cache)
    req_code = effective_code(db, requester, req_date, cache)
    tgt_code = effective_code(db, target, tgt_date, cache)
    return {
        "blocked": blocked,
        "warnings": [] if blocked else swap_warnings(db, requester, target, req_date, tgt_date),
        "kind": _kind(req_date, tgt_date, req_code, tgt_code),
    }


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
    """Assess each pair on its own: blocked reason, rest warnings and kind."""
    target = db.get(User, body.target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Användaren hittades inte")
    cache: dict = {}
    return {"results": [_assess(db, current_user, target, p.req_date, p.tgt_date, cache) for p in body.pairs]}


def _effective_days(db: Session, user: User, start: date_cls, end: date_cls) -> dict:
    """{date: {"code", "start", "end"}} from build_week_data, so overrides and swaps count."""
    days = {}
    for monday in range(start.toordinal() - start.weekday(), end.toordinal() + 1, 7):
        week = date_cls.fromordinal(monday).isocalendar()
        for info in build_week_data(week[0], week[1], person_id=user.rotation_person_id, session=db):
            shift = info.get("shift")
            code = shift.code if shift else "OFF"
            start_dt = end_dt = None
            if code != "OFF":
                _, start_dt, end_dt = calculate_shift_hours(info["date"], shift)
            days[info["date"]] = {
                "code": code,
                "start": start_dt.strftime("%H:%M") if start_dt else None,
                "end": end_dt.strftime("%H:%M") if end_dt else None,
            }
    return days


@router.get("/api/compare")
async def compare_days(
    target_id: int,
    start: date_cls | None = None,
    weeks: int = 4,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Both users' effective days side by side, from today at the earliest, 1 to 6 weeks."""
    target = db.get(User, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Användaren hittades inte")
    if not _can_swap(target):
        raise HTTPException(status_code=404, detail="Användaren hittades inte")
    if not _can_swap(current_user):
        raise HTTPException(status_code=400, detail="Du kan inte byta pass")
    if start and start > get_today() + timedelta(days=MAX_START_DAYS):
        raise HTTPException(status_code=400, detail="Ogiltigt startdatum")
    first = max(start or get_today(), get_today())
    last = first + timedelta(days=7 * min(max(weeks, 1), MAX_WEEKS) - 1)
    mine = _effective_days(db, current_user, first, last)
    theirs = _effective_days(db, target, first, last)
    days = []
    for n in range((last - first).days + 1):
        d = first + timedelta(days=n)
        days.append({"date": d.isoformat(), "me": mine[d], "them": theirs[d]})
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
        verdict = _assess(db, current_user, colleague, date, date, cache)
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
        pairs = []
        for r in members:
            same_day = r.requester_date == r.target_date
            req_free = r.requester_shift_code == "OFF" and same_day
            tgt_free = r.target_shift_code == "OFF" and same_day
            req_side = _side(r.requester_shift_code, r.requester_date, req_free)
            tgt_side = _side(r.target_shift_code, r.target_date, tgt_free)
            pairs.append(
                {
                    "give": req_side if mine_is_requester else tgt_side,
                    "take": tgt_side if mine_is_requester else req_side,
                    "warnings": swap_warnings(db, r.requester, r.target, r.requester_date, r.target_date)
                    if pending
                    else [],
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
        db.add(
            ShiftSwap(
                requester_id=current_user.id,
                target_id=target_id,
                requester_date=rd,
                target_date=td,
                requester_shift_code=effective_code(db, current_user, rd, cache),
                target_shift_code=effective_code(db, target_user, td, cache),
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
    codes = []
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
        req_code = effective_code(db, swap.requester, swap.requester_date, cache)
        tgt_code = effective_code(db, swap.target, swap.target_date, cache)
        if (req_code, tgt_code) != (swap.requester_shift_code, swap.target_shift_code):
            raise HTTPException(status_code=400, detail="Schemat har ändrats sedan förslaget")
        codes.append((req_code, tgt_code))

    now = utcnow()
    for swap, (req_code, tgt_code) in zip(group, codes, strict=True):
        # Codes come from the cache filled before any write, so earlier pairs cannot leak in.
        _apply_swap_as_overrides(db, swap, req_code, tgt_code, cache)
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
    db.query(ShiftOverride).filter(ShiftOverride.swap_id.in_([s.id for s in group])).delete()
    now = utcnow()
    for swap in group:
        swap.status = SwapStatus.CANCELLED
        swap.responded_at = now
    db.commit()
    clear_schedule_cache()

    return RedirectResponse(url="/swaps", status_code=303)
