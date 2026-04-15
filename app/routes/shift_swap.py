# app/routes/shift_swap.py
"""Shift swap management routes - propose, accept, reject, cancel swaps."""

from datetime import datetime, timedelta
from datetime import time as dt_time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user
from app.core.helpers import render_template
from app.core.schedule import build_week_data, calculate_shift_hours, clear_schedule_cache
from app.core.schedule.core import determine_shift_for_date
from app.core.utils import get_today
from app.database.database import ShiftSwap, SwapStatus, User, get_db
from app.routes.shared import templates

router = APIRouter(prefix="/swaps", tags=["shift_swaps"])

_MIN_REST_HOURS = 11


def _get_shift_times_from_session(date, rotation_person_id, session):
    """Return (start_dt, end_dt) for a person's effective shift on date, respecting accepted swaps."""
    week_data = build_week_data(
        date.isocalendar()[0], date.isocalendar()[1], person_id=rotation_person_id, session=session
    )
    day = next((d for d in week_data if d["date"] == date), None)
    if not day:
        return None, None
    shift = day.get("shift")
    if not shift or not shift.start_time or not shift.end_time:
        return None, None
    _, start_dt, end_dt = calculate_shift_hours(date, shift)
    return start_dt, end_dt


def _check_rest_ok(work_date, shift_code, rotation_person_id, session=None):
    """Return False if working shift_code on work_date violates 11h rest against adjacent days.

    If session is provided, uses build_week_data (includes accepted swaps) for adjacent days.
    Otherwise falls back to determine_shift_for_date (raw rotation only).
    """
    _, new_start, new_end = calculate_shift_hours(work_date, shift_code)
    if not new_start or not new_end:
        return True  # No time data for this shift — cannot validate, allow

    if session:
        _, prev_end = _get_shift_times_from_session(work_date - timedelta(days=1), rotation_person_id, session)
        next_start, _ = _get_shift_times_from_session(work_date + timedelta(days=1), rotation_person_id, session)
    else:
        prev_result = determine_shift_for_date(work_date - timedelta(days=1), rotation_person_id)
        if prev_result and prev_result[0] and prev_result[0].code not in ("OFF", "OC"):
            _, _, prev_end = calculate_shift_hours(work_date - timedelta(days=1), prev_result[0])
        else:
            prev_end = None
        next_result = determine_shift_for_date(work_date + timedelta(days=1), rotation_person_id)
        if next_result and next_result[0] and next_result[0].code not in ("OFF", "OC"):
            _, next_start, _ = calculate_shift_hours(work_date + timedelta(days=1), next_result[0])
        else:
            next_start = None

    if prev_end and (new_start - prev_end).total_seconds() / 3600 < _MIN_REST_HOURS:
        return False
    if next_start and (next_start - new_end).total_seconds() / 3600 < _MIN_REST_HOURS:
        return False

    return True


_MIN_WEEKLY_REST_HOURS = 36


def _check_weekly_rest_ok(work_date, new_shift_code, rotation_person_id, original_shift_code=None):
    """Return False if any rolling 7-day period containing work_date has < 36h consecutive rest
    that wasn't already violated by the original shift.

    ATL §14 uses rolling sjudagarsperioder (day 1-7, day 2-8, ...).
    There are 7 possible windows containing work_date: [D-6..D], [D-5..D+1], ..., [D..D+6].
    All must be checked. OC (beredskap) blocks weekly rest per ATL §14.

    If original_shift_code is provided (same-day swap replacing an existing shift), a window
    that already violated 36h with the original shift is not counted as a new violation.
    The swap is blocked only if it makes weekly rest worse than the original schedule.
    """

    def _max_gap_in_window(shift_code_on_work_date, w_start, w_end):
        """Compute the longest consecutive rest gap (hours) within [w_start, w_end]."""
        intervals = []
        for day_offset in range(-6, 7):
            d = work_date + timedelta(days=day_offset)
            if d == work_date:
                code = shift_code_on_work_date
            else:
                result = determine_shift_for_date(d, rotation_person_id)
                code = result[0].code if result and result[0] else "OFF"

            if code == "OFF":
                continue

            _, start_dt, end_dt = calculate_shift_hours(d, code)
            if start_dt and end_dt:
                clipped_s = max(start_dt, w_start)
                clipped_e = min(end_dt, w_end)
                if clipped_s < clipped_e:
                    intervals.append((clipped_s, clipped_e))

        if not intervals:
            return 168.0  # No shifts — full week free

        intervals.sort()
        gap = max(
            (intervals[0][0] - w_start).total_seconds() / 3600,
            *((intervals[i + 1][0] - intervals[i][1]).total_seconds() / 3600 for i in range(len(intervals) - 1)),
            (w_end - intervals[-1][1]).total_seconds() / 3600,
        )
        return gap

    for window_start_offset in range(-6, 1):  # windows starting D-6 to D
        w_start = datetime.combine(work_date + timedelta(days=window_start_offset), dt_time(0, 0))
        w_end = w_start + timedelta(hours=168)

        new_gap = _max_gap_in_window(new_shift_code, w_start, w_end)
        if new_gap >= _MIN_WEEKLY_REST_HOURS:
            continue  # This window is fine

        if original_shift_code is not None:
            # Same-day swap: only block if the new shift makes weekly rest worse
            orig_gap = _max_gap_in_window(original_shift_code, w_start, w_end)
            if new_gap >= orig_gap:
                continue  # Not worse than before — allow
            if orig_gap < _MIN_WEEKLY_REST_HOURS:
                continue  # Window already violated before swap — pre-existing issue

        return False  # New violation introduced by this swap

    return True


@router.get("/api/shifts/{user_id}")
async def get_user_shifts(
    user_id: int,
    offering: str = None,
    ref_date: str = None,
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
    center = datetime.strptime(ref_date, "%Y-%m-%d").date() if ref_date else today
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
        if not tgt_shift or tgt_shift.code == "OFF":
            continue

        is_same_day = d == center

        if requester_working_on_center:
            # Requester is working on center — only same-day swap is relevant
            if not is_same_day:
                continue
            # Same-day swap: both must be working different shifts
            if not my_shift:
                continue
            if tgt_shift.code == my_code:
                continue  # Same shift code — pointless swap
            # Don't mix OC and regular shifts
            if (my_code == "OC") != (tgt_shift.code == "OC"):
                continue
        else:
            # Requester is free — can take target's shift on a different day

            # OC-to-OC: when offering OC, only show target's OC shifts (requester must be OFF)
            # Regular: when offering a regular shift, only show target's regular shifts
            if offering == "OC":
                if tgt_shift.code != "OC":
                    continue  # Offering OC — only interested in target's OC
                if my_code != "OFF":
                    continue  # Must be OFF to take their OC
            else:
                if tgt_shift.code == "OC":
                    continue  # Offering regular — can't take OC
                if my_code != "OFF":
                    continue  # Must be OFF to take their shift

        # Calculate target shift times (the shift I would work)
        _, tgt_start, tgt_end = calculate_shift_hours(d, tgt_shift)

        # Same-day swaps skip rest checks: both parties already work that day,
        # only the shift assignment changes.
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
            if not _check_weekly_rest_ok(d, tgt_shift.code, my_pid):
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
                "code": tgt_shift.code,
                "label": tgt_shift.label or tgt_shift.code,
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

    return render_template(
        templates,
        "shift_swaps.html",
        request,
        {"sent_swaps": sent, "received_swaps": received, "pending_count": pending_count},
        user=current_user,
    )


@router.post("/propose")
async def propose_swap(
    target_id: int = Form(...),
    requester_date: str = Form(...),
    target_date: str = Form(...),
    message: str = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Propose a shift swap: give your shift on requester_date, take target's shift on target_date."""
    if target_id == current_user.id:
        raise HTTPException(status_code=400, detail="Kan inte byta pass med dig själv")

    req_date = datetime.strptime(requester_date, "%Y-%m-%d").date()
    tgt_date = datetime.strptime(target_date, "%Y-%m-%d").date()
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

    # Get shift codes for both dates
    req_result = determine_shift_for_date(req_date, start_week=current_user.rotation_person_id)
    tgt_result = determine_shift_for_date(tgt_date, start_week=target_user.rotation_person_id)

    req_shift = req_result[0] if req_result and req_result[0] else None
    tgt_shift = tgt_result[0] if tgt_result and tgt_result[0] else None

    if not req_shift or req_shift.code in ("OFF",):
        raise HTTPException(status_code=400, detail="Du jobbar inte det datumet")
    if not tgt_shift or tgt_shift.code in ("OFF",):
        raise HTTPException(status_code=400, detail="Kollegan jobbar inte det datumet")

    is_same_day = req_date == tgt_date
    if is_same_day:
        if req_shift.code == tgt_shift.code:
            raise HTTPException(status_code=400, detail="Ni jobbar redan samma pass")
        if (req_shift.code == "OC") != (tgt_shift.code == "OC"):
            raise HTTPException(status_code=400, detail="Kan inte blanda OC och vanliga pass")

    # Same-day swaps skip rest checks: both parties already work that day.
    if not is_same_day:
        if not _check_rest_ok(tgt_date, tgt_shift.code, current_user.rotation_person_id, session=db):
            raise HTTPException(status_code=400, detail="Bryter mot 11 timmars dygnsvila (du)")
        if not _check_rest_ok(req_date, req_shift.code, target_user.rotation_person_id, session=db):
            raise HTTPException(status_code=400, detail="Bryter mot 11 timmars dygnsvila (kollegan)")
        if not _check_weekly_rest_ok(tgt_date, tgt_shift.code, current_user.rotation_person_id):
            raise HTTPException(status_code=400, detail="Bryter mot 36 timmars veckovila (du)")
        if not _check_weekly_rest_ok(req_date, req_shift.code, target_user.rotation_person_id):
            raise HTTPException(status_code=400, detail="Bryter mot 36 timmars veckovila (kollegan)")

    swap = ShiftSwap(
        requester_id=current_user.id,
        target_id=target_id,
        requester_date=req_date,
        target_date=tgt_date,
        requester_shift_code=req_result[0].code if req_result and req_result[0] else None,
        target_shift_code=tgt_result[0].code if tgt_result and tgt_result[0] else None,
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

    swap.status = SwapStatus.ACCEPTED
    swap.responded_at = datetime.utcnow()
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
    swap.responded_at = datetime.utcnow()
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
    swap.responded_at = datetime.utcnow()
    db.commit()
    clear_schedule_cache()

    return RedirectResponse(url="/swaps", status_code=303)
