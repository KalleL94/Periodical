# app/routes/shift_swap.py
"""Shift swap management routes - propose, accept, reject, cancel swaps."""

from datetime import date as date_cls
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user
from app.core.schedule import build_week_data, clear_schedule_cache
from app.core.swaps import check_swap, shift_code_on, validate_proposal
from app.core.utils import get_today
from app.database.database import ShiftOverride, ShiftSwap, SwapStatus, User, get_db, utcnow
from app.routes.shared import render

router = APIRouter(prefix="/swaps", tags=["shift_swaps"])


def _apply_swap_as_overrides(db: Session, swap: ShiftSwap, req_code: str, tgt_code: str) -> None:
    """Write the swap as ShiftOverride rows, the same result the legacy swap_map gives.

    On each date the two people exchange their rotation shifts. A same-day swap
    collapses to two rows. "OFF" is written as a code so a shift given away stays free.
    """
    requester, target = swap.requester, swap.target
    rd, td = swap.requester_date, swap.target_date
    rows = {
        (requester.id, rd): shift_code_on(rd, target.rotation_person_id),
        (target.id, rd): req_code,
        (target.id, td): shift_code_on(td, requester.rotation_person_id),
        (requester.id, td): tgt_code,  # same day: overwrites the first key
    }
    for (user_id, day), code in rows.items():
        db.add(ShiftOverride(user_id=user_id, date=day, shift_code=code, swap_id=swap.id, created_by=target.id))


@router.get("/api/shifts/{user_id}")
async def get_user_shifts(
    user_id: int,
    offering: str = None,
    ref_date: date_cls | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return target's shifts within ±3 months of ref_date that make sense to swap for.

    Each item carries "blocked": the check_swap reason the swap would be refused, or None.
    The requester gives ref_date and takes the listed date, as the propose form sends them.
    """
    from app.core.schedule import calculate_ob_hours, calculate_shift_hours
    from app.core.schedule.ob import get_combined_rules_for_year

    target = db.query(User).get(user_id)
    if not target:
        return JSONResponse(content={"shifts": []})

    today = get_today()
    center = ref_date or today
    target_pid = target.rotation_person_id
    my_pid = current_user.rotation_person_id

    # Build list of days: ±90 days from center, but not in the past
    scan_start = max(center - timedelta(days=90), today + timedelta(days=1))
    scan_end = center + timedelta(days=90)
    total_days = (scan_end - scan_start).days + 1

    # Fetch week data for both users once per ISO week (center included for the same-day check)
    target_weeks = {}
    my_weeks = {}
    for d in [center] + [scan_start + timedelta(days=i) for i in range(total_days)]:
        wk = d.isocalendar()[:2]
        if wk not in my_weeks:
            target_weeks[wk] = {day["date"]: day for day in build_week_data(*wk, person_id=target_pid, session=db)}
            my_weeks[wk] = {day["date"]: day for day in build_week_data(*wk, person_id=my_pid, session=db)}

    # Determine if requester is working on center (only same-day swap is offered then)
    center_wk = center.isocalendar()[:2]
    center_my_info = my_weeks.get(center_wk, {}).get(center, {})
    center_my_shift = center_my_info.get("shift")
    center_my_code = center_my_shift.code if center_my_shift else "OFF"
    requester_working_on_center = center_my_code not in ("OFF", "OC")

    shifts = []
    ob_rules_cache = {}
    for day_offset in range(total_days):
        d = scan_start + timedelta(days=day_offset)
        wk = d.isocalendar()[:2]

        # Determine what I have on this day
        my_info = my_weeks[wk].get(d, {})
        my_shift = my_info.get("shift")
        my_code = my_shift.code if my_shift else "OFF"

        # Check target's shift
        tgt_info = target_weeks[wk].get(d, {})
        tgt_shift = tgt_info.get("shift")
        tgt_code = tgt_shift.code if tgt_shift else "OFF"

        is_same_day = d == center

        # Skip days where target is free, unless it is a same-day give-away
        if tgt_code == "OFF" and not (is_same_day and requester_working_on_center):
            continue

        if requester_working_on_center:
            # Requester is working on center, so only same-day is relevant
            if not is_same_day:
                continue
            if tgt_code == "OFF":
                # Give-away: target is free, requester gives their shift away
                shifts.append(
                    {
                        "date": d.isoformat(),
                        "date_display": d.strftime("%a %d %b"),
                        "code": "OFF",
                        "label": "Ledig",
                        "ob_hours": 0.0,
                        "blocked": check_swap(db, current_user, target, center, d),
                    }
                )
                continue
            # Mutual same-day swap: different shifts, no OC mixing
            if not my_shift:
                continue
            if tgt_code == my_code:
                continue  # Same shift code, pointless swap
            if (my_code == "OC") != (tgt_code == "OC"):
                continue
        else:
            # Requester is free, can take target's shift on a different day

            # OC-to-OC: when offering OC, only show target's OC shifts (requester must be OFF)
            # Regular: when offering a regular shift, only show target's regular shifts
            if offering == "OC":
                if tgt_code != "OC":
                    continue  # Offering OC, only interested in target's OC
                if my_code != "OFF":
                    continue  # Must be OFF to take their OC
            else:
                if tgt_code == "OC":
                    continue  # Offering regular, can't take OC
                if my_code != "OFF":
                    continue  # Must be OFF to take their shift

        # Calculate target shift times (the shift I would work)
        _, tgt_start, tgt_end = calculate_shift_hours(d, tgt_shift) if tgt_shift else (None, None, None)

        # Calculate OB hours for the target's shift
        ob_total = 0.0
        if tgt_start and tgt_end:
            year = d.year
            if year not in ob_rules_cache:
                ob_rules_cache[year] = get_combined_rules_for_year(year)
            ob_dict = calculate_ob_hours(tgt_start, tgt_end, ob_rules_cache[year])
            ob_total = sum(ob_dict.values())

        shifts.append(
            {
                "date": d.isoformat(),
                "date_display": d.strftime("%a %d %b"),
                "code": tgt_code,
                "label": (tgt_shift.label or tgt_code) if tgt_shift else tgt_code,
                "ob_hours": round(ob_total, 1),
                # The rules (rest, slot conflicts, OC) live in check_swap; failing days stay listed, greyed.
                "blocked": check_swap(db, current_user, target, center, d),
            }
        )

    return JSONResponse(content={"shifts": shifts})


@router.get("/", response_class=HTMLResponse)
async def list_swaps(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all swaps for the current user (both sent and received)."""
    sent = (
        db.query(ShiftSwap)
        .filter(ShiftSwap.requester_id == current_user.id)
        .order_by(ShiftSwap.created_at.desc())
        .all()
    )
    received = (
        db.query(ShiftSwap).filter(ShiftSwap.target_id == current_user.id).order_by(ShiftSwap.created_at.desc()).all()
    )

    pending_count = sum(1 for s in received if s.status == SwapStatus.PENDING)

    return render(
        "shift_swaps.html",
        {
            "request": request,
            "user": current_user,
            "sent_swaps": sent,
            "received_swaps": received,
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
    if len(req_date) != len(tgt_date):
        raise HTTPException(status_code=400, detail="Ogiltigt förslag")

    pairs = list(zip(req_date, tgt_date, strict=True))
    reason = validate_proposal(db, current_user, target_user, pairs)
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
                requester_shift_code=shift_code_on(rd, current_user.rotation_person_id),
                target_shift_code=shift_code_on(td, target_user.rotation_person_id),
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
    for swap in group:
        reason = check_swap(
            db, swap.requester, swap.target, swap.requester_date, swap.target_date, exclude_swap_ids=group_ids
        )
        if reason:
            raise HTTPException(status_code=400, detail=reason)
        req_code = shift_code_on(swap.requester_date, swap.requester.rotation_person_id)
        tgt_code = shift_code_on(swap.target_date, swap.target.rotation_person_id)
        if (req_code, tgt_code) != (swap.requester_shift_code, swap.target_shift_code):
            raise HTTPException(status_code=400, detail="Schemat har ändrats sedan förslaget")
        codes.append((req_code, tgt_code))

    now = utcnow()
    for swap, (req_code, tgt_code) in zip(group, codes, strict=True):
        _apply_swap_as_overrides(db, swap, req_code, tgt_code)
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
