# app/routes/shift_swap.py
"""Shift swap management routes - propose, accept, reject, cancel swaps."""

from datetime import date as date_cls
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user
from app.core.schedule import build_week_data, clear_schedule_cache
from app.core.schedule.core import determine_shift_for_date
from app.core.swaps import (
    SLOT_TAKEN,
    check_rest_ok,
    check_weekly_rest_ok,
    find_active_swap_conflict,
    swap_participant_dates,
)
from app.core.utils import get_today
from app.database.database import ShiftSwap, SwapStatus, User, get_db, utcnow
from app.routes.shared import render

router = APIRouter(prefix="/swaps", tags=["shift_swaps"])


def _raise_if_swap_conflicts(
    db: Session,
    requester_id: int,
    target_id: int,
    requester_date,
    target_date,
    statuses: tuple[SwapStatus, ...],
    exclude_swap_id: int | None = None,
) -> None:
    participant_dates = swap_participant_dates(requester_id, target_id, requester_date, target_date)
    if find_active_swap_conflict(db, participant_dates, statuses, exclude_swap_id):
        raise HTTPException(status_code=400, detail=SLOT_TAKEN)


@router.get("/api/shifts/{user_id}")
async def get_user_shifts(
    user_id: int,
    offering: str = None,
    ref_date: date_cls | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return target's shifts within ±3 months of ref_date, only on days the requester is free."""
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

    # Fetch week data for both users, grouped by ISO week
    # Include adjacent days for 11h rest rule checks
    target_weeks = {}
    my_weeks = {}
    for day_offset in range(total_days):
        d = scan_start + timedelta(days=day_offset)
        for adj in (d - timedelta(days=1), d, d + timedelta(days=1)):
            iso = adj.isocalendar()
            wk = (iso[0], iso[1])
            if wk not in my_weeks:
                target_weeks[wk] = {
                    day["date"]: day for day in build_week_data(wk[0], wk[1], person_id=target_pid, session=db)
                }
                my_weeks[wk] = {day["date"]: day for day in build_week_data(wk[0], wk[1], person_id=my_pid, session=db)}

    MIN_REST_HOURS = 11

    # Determine if requester is working on center (only same-day swap is offered then)
    center_wk = center.isocalendar()[:2]
    center_my_info = my_weeks.get(center_wk, {}).get(center, {})
    center_my_shift = center_my_info.get("shift")
    center_my_code = center_my_shift.code if center_my_shift else "OFF"
    requester_working_on_center = center_my_code not in ("OFF", "OC")

    def get_my_shift_times(d):
        """Get start/end datetimes for my shift on date d."""
        iso = d.isocalendar()
        wk = (iso[0], iso[1])
        info = my_weeks.get(wk, {}).get(d, {})
        s = info.get("shift")
        if not s or not s.start_time or not s.end_time:
            return None, None
        _, start_dt, end_dt = calculate_shift_hours(d, s)
        return start_dt, end_dt

    shifts = []
    ob_rules_cache = {}
    for day_offset in range(total_days):
        d = scan_start + timedelta(days=day_offset)
        iso = d.isocalendar()
        wk = (iso[0], iso[1])

        # Determine what I have on this day
        my_info = my_weeks[wk].get(d, {})
        my_shift = my_info.get("shift")
        my_code = my_shift.code if my_shift else "OFF"

        # Check target's shift
        tgt_info = target_weeks[wk].get(d, {})
        tgt_shift = tgt_info.get("shift")
        tgt_code = tgt_shift.code if tgt_shift else "OFF"

        is_same_day = d == center

        # Skip days where target is free — unless it's a same-day give-away
        if tgt_code == "OFF" and not (is_same_day and requester_working_on_center):
            continue

        if requester_working_on_center:
            # Requester is working on center — only same-day is relevant
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
                    }
                )
                continue
            # Mutual same-day swap: different shifts, no OC mixing
            if not my_shift:
                continue
            if tgt_code == my_code:
                continue  # Same shift code — pointless swap
            if (my_code == "OC") != (tgt_code == "OC"):
                continue
        else:
            # Requester is free — can take target's shift on a different day

            # OC-to-OC: when offering OC, only show target's OC shifts (requester must be OFF)
            # Regular: when offering a regular shift, only show target's regular shifts
            if offering == "OC":
                if tgt_code != "OC":
                    continue  # Offering OC — only interested in target's OC
                if my_code != "OFF":
                    continue  # Must be OFF to take their OC
            else:
                if tgt_code == "OC":
                    continue  # Offering regular — can't take OC
                if my_code != "OFF":
                    continue  # Must be OFF to take their shift

        # Calculate target shift times (the shift I would work)
        _, tgt_start, tgt_end = calculate_shift_hours(d, tgt_shift) if tgt_shift else (None, None, None)

        # Same-day operations skip rest checks.
        if not is_same_day:
            # 11h rest rule: check against my shifts on adjacent days
            if tgt_start and tgt_end:
                rest_ok = True
                prev_start, prev_end = get_my_shift_times(d - timedelta(days=1))
                if prev_end and (tgt_start - prev_end).total_seconds() / 3600 < MIN_REST_HOURS:
                    rest_ok = False
                next_start, next_end = get_my_shift_times(d + timedelta(days=1))
                if rest_ok and next_start and (next_start - tgt_end).total_seconds() / 3600 < MIN_REST_HOURS:
                    rest_ok = False
                if not rest_ok:
                    continue

            # 36h weekly rest rule: OC (beredskap) blocks weekly rest per ATL §14
            if not check_weekly_rest_ok(d, tgt_code, my_pid):
                continue

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


@router.post("/propose")
async def propose_swap(
    target_id: int = Form(...),
    requester_date: date_cls = Form(...),
    target_date: date_cls = Form(...),
    message: str = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Propose a shift swap: give your shift on requester_date, take target's shift on target_date."""
    if target_id == current_user.id:
        raise HTTPException(status_code=400, detail="Kan inte byta pass med dig själv")

    req_date = requester_date
    tgt_date = target_date
    today = get_today()

    if req_date <= today or tgt_date <= today:
        raise HTTPException(status_code=400, detail="Kan bara byta framtida pass")

    target_user = db.query(User).get(target_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="Användaren hittades inte")

    # Check no duplicate pending swap
    existing = (
        db.query(ShiftSwap)
        .filter(
            ShiftSwap.requester_id == current_user.id,
            ShiftSwap.target_id == target_id,
            ShiftSwap.requester_date == req_date,
            ShiftSwap.target_date == tgt_date,
            ShiftSwap.status == SwapStatus.PENDING,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Byte redan föreslaget för dessa datum")

    _raise_if_swap_conflicts(
        db,
        current_user.id,
        target_id,
        req_date,
        tgt_date,
        (SwapStatus.PENDING, SwapStatus.ACCEPTED),
    )

    # Get shift codes for both dates
    req_result = determine_shift_for_date(req_date, start_week=current_user.rotation_person_id)
    tgt_result = determine_shift_for_date(tgt_date, start_week=target_user.rotation_person_id)

    req_shift = req_result[0] if req_result and req_result[0] else None
    tgt_shift = tgt_result[0] if tgt_result and tgt_result[0] else None

    req_code = req_shift.code if req_shift else "OFF"
    tgt_code = tgt_shift.code if tgt_shift else "OFF"
    is_same_day = req_date == tgt_date
    # One-way transfer: same day, exactly one party is free (XOR)
    is_one_way = is_same_day and (req_code == "OFF") != (tgt_code == "OFF")

    if not is_same_day:
        # Cross-day swaps require both to be working
        if req_code == "OFF":
            raise HTTPException(status_code=400, detail="Du jobbar inte det datumet")
        if tgt_code == "OFF":
            raise HTTPException(status_code=400, detail="Kollegan jobbar inte det datumet")
    else:
        # Same-day: both free = pointless; mutual swap has extra rules
        if req_code == "OFF" and tgt_code == "OFF":
            raise HTTPException(status_code=400, detail="Ingen av er jobbar den dagen")
        if not is_one_way:
            if req_code == tgt_code:
                raise HTTPException(status_code=400, detail="Ni jobbar redan samma pass")
            if (req_code == "OC") != (tgt_code == "OC"):
                raise HTTPException(status_code=400, detail="Kan inte blanda OC och vanliga pass")

    # Same-day operations (mutual or one-way) skip rest checks.
    if not is_same_day:
        if not check_rest_ok(tgt_date, tgt_code, current_user.rotation_person_id, session=db):
            raise HTTPException(status_code=400, detail="Bryter mot 11 timmars dygnsvila (du)")
        if not check_rest_ok(req_date, req_code, target_user.rotation_person_id, session=db):
            raise HTTPException(status_code=400, detail="Bryter mot 11 timmars dygnsvila (kollegan)")
        if not check_weekly_rest_ok(tgt_date, tgt_code, current_user.rotation_person_id):
            raise HTTPException(status_code=400, detail="Bryter mot 36 timmars veckovila (du)")
        if not check_weekly_rest_ok(req_date, req_code, target_user.rotation_person_id):
            raise HTTPException(status_code=400, detail="Bryter mot 36 timmars veckovila (kollegan)")

    swap = ShiftSwap(
        requester_id=current_user.id,
        target_id=target_id,
        requester_date=req_date,
        target_date=tgt_date,
        requester_shift_code=req_code,
        target_shift_code=tgt_code,
        message=message,
    )
    db.add(swap)
    db.commit()

    return RedirectResponse(url="/swaps", status_code=303)


@router.post("/{swap_id}/accept")
async def accept_swap(
    swap_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Accept a swap request (target user only)."""
    swap = db.query(ShiftSwap).get(swap_id)
    if not swap:
        raise HTTPException(status_code=404, detail="Bytet hittades inte")
    if swap.target_id != current_user.id:
        raise HTTPException(status_code=403, detail="Inte behörig")
    if swap.status != SwapStatus.PENDING:
        raise HTTPException(status_code=400, detail="Bytet är inte längre väntande")

    _raise_if_swap_conflicts(
        db,
        swap.requester_id,
        swap.target_id,
        swap.requester_date,
        swap.target_date,
        (SwapStatus.ACCEPTED,),
        exclude_swap_id=swap.id,
    )

    swap.status = SwapStatus.ACCEPTED
    swap.responded_at = utcnow()
    db.commit()
    clear_schedule_cache()

    return RedirectResponse(url="/swaps", status_code=303)


@router.post("/{swap_id}/reject")
async def reject_swap(
    swap_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reject a swap request (target user only)."""
    swap = db.query(ShiftSwap).get(swap_id)
    if not swap:
        raise HTTPException(status_code=404, detail="Bytet hittades inte")
    if swap.target_id != current_user.id:
        raise HTTPException(status_code=403, detail="Inte behörig")
    if swap.status != SwapStatus.PENDING:
        raise HTTPException(status_code=400, detail="Bytet är inte längre väntande")

    swap.status = SwapStatus.REJECTED
    swap.responded_at = utcnow()
    db.commit()

    return RedirectResponse(url="/swaps", status_code=303)


@router.post("/{swap_id}/cancel")
async def cancel_swap(
    swap_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel a pending or accepted swap (requester, target, or admin)."""
    swap = db.query(ShiftSwap).get(swap_id)
    if not swap:
        raise HTTPException(status_code=404, detail="Bytet hittades inte")

    is_participant = swap.requester_id == current_user.id or swap.target_id == current_user.id
    is_admin = current_user.role.value == "admin"

    if not is_participant and not is_admin:
        raise HTTPException(status_code=403, detail="Inte behörig")
    if swap.status not in (SwapStatus.PENDING, SwapStatus.ACCEPTED):
        raise HTTPException(status_code=400, detail="Kan bara avbryta väntande eller accepterade byten")

    swap.status = SwapStatus.CANCELLED
    swap.responded_at = utcnow()
    db.commit()
    clear_schedule_cache()

    return RedirectResponse(url="/swaps", status_code=303)
