# app/core/swaps.py
"""Rules for shift swaps, shared by the day picker, propose and accept."""

from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.core.schedule import build_week_data, calculate_shift_hours
from app.core.schedule.core import determine_shift_for_date
from app.core.schedule.vacation import get_vacation_dates_for_year
from app.core.utils import get_today
from app.database.database import Absence, ShiftOverride, ShiftSwap, SwapStatus, User

MIN_REST_HOURS = 11
MIN_WEEKLY_REST_HOURS = 36

SLOT_TAKEN = "Ett byte finns redan för någon av personerna på dessa datum"


def _get_shift_times_from_session(day, rotation_person_id, session):
    """Return (start_dt, end_dt) for a person's effective shift on day, respecting accepted swaps."""
    week_data = build_week_data(
        day.isocalendar()[0], day.isocalendar()[1], person_id=rotation_person_id, session=session
    )
    info = next((d for d in week_data if d["date"] == day), None)
    if not info:
        return None, None
    shift = info.get("shift")
    if not shift or not shift.start_time or not shift.end_time:
        return None, None
    _, start_dt, end_dt = calculate_shift_hours(day, shift)
    return start_dt, end_dt


def _rotation_times(day, rotation_person_id):
    """Return (start_dt, end_dt) for the raw rotation shift on day, or (None, None) for OFF/OC."""
    result = determine_shift_for_date(day, rotation_person_id)
    if result and result[0] and result[0].code not in ("OFF", "OC"):
        _, start_dt, end_dt = calculate_shift_hours(day, result[0])
        return start_dt, end_dt
    return None, None


def check_rest_ok(work_date, shift_code, rotation_person_id, session=None, off_dates: frozenset[date] = frozenset()):
    """Return False if working shift_code on work_date violates 11h rest against adjacent days.

    If session is provided, uses build_week_data (includes accepted swaps) for adjacent days.
    Otherwise falls back to determine_shift_for_date (raw rotation only).
    A neighbouring date in off_dates counts as OFF (the shift there is given away).
    """
    _, new_start, new_end = calculate_shift_hours(work_date, shift_code)
    if not new_start or not new_end:
        return True  # No time data for this shift, cannot validate, allow

    def times(day):
        if day in off_dates:
            return None, None
        if session:
            return _get_shift_times_from_session(day, rotation_person_id, session)
        return _rotation_times(day, rotation_person_id)

    _, prev_end = times(work_date - timedelta(days=1))
    next_start, _ = times(work_date + timedelta(days=1))

    if prev_end and (new_start - prev_end).total_seconds() / 3600 < MIN_REST_HOURS:
        return False
    if next_start and (next_start - new_end).total_seconds() / 3600 < MIN_REST_HOURS:
        return False
    return True


def check_weekly_rest_ok(
    work_date,
    new_shift_code,
    rotation_person_id,
    original_shift_code=None,
    off_dates: frozenset[date] = frozenset(),
):
    """Return False if any rolling 7-day period containing work_date has < 36h consecutive rest
    that wasn't already violated by the original shift.

    ATL §14 uses rolling sjudagarsperioder (day 1-7, day 2-8, ...).
    There are 7 possible windows containing work_date: [D-6..D], [D-5..D+1], ..., [D..D+6].
    All must be checked. OC (beredskap) blocks weekly rest per ATL §14.

    If original_shift_code is provided (same-day swap replacing an existing shift), a window
    that already violated 36h with the original shift is not counted as a new violation.
    The swap is blocked only if it makes weekly rest worse than the original schedule.
    A date in off_dates counts as OFF (the shift there is given away).
    """

    def _max_gap_in_window(shift_code_on_work_date, w_start, w_end):
        """Compute the longest consecutive rest gap (hours) within [w_start, w_end]."""
        intervals = []
        for day_offset in range(-6, 7):
            d = work_date + timedelta(days=day_offset)
            if d == work_date:
                code = shift_code_on_work_date
            elif d in off_dates:
                code = "OFF"
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
            return 168.0  # No shifts, full week free

        intervals.sort()
        return max(
            (intervals[0][0] - w_start).total_seconds() / 3600,
            *((intervals[i + 1][0] - intervals[i][1]).total_seconds() / 3600 for i in range(len(intervals) - 1)),
            (w_end - intervals[-1][1]).total_seconds() / 3600,
        )

    for window_start_offset in range(-6, 1):  # windows starting D-6 to D
        w_start = datetime.combine(work_date + timedelta(days=window_start_offset), time(0, 0))
        w_end = w_start + timedelta(hours=168)

        new_gap = _max_gap_in_window(new_shift_code, w_start, w_end)
        if new_gap >= MIN_WEEKLY_REST_HOURS:
            continue  # This window is fine

        if original_shift_code is not None:
            # Same-day swap: only block if the new shift makes weekly rest worse
            orig_gap = _max_gap_in_window(original_shift_code, w_start, w_end)
            if new_gap >= orig_gap:
                continue  # Not worse than before, allow
            if orig_gap < MIN_WEEKLY_REST_HOURS:
                continue  # Window already violated before swap, pre-existing issue

        return False  # New violation introduced by this swap

    return True


def swap_participant_dates(requester_id: int, target_id: int, requester_date, target_date) -> set[tuple[int, object]]:
    """Return every user/date slot affected by a swap."""
    return {
        (requester_id, requester_date),
        (target_id, requester_date),
        (requester_id, target_date),
        (target_id, target_date),
    }


def find_active_swap_conflict(
    db: Session,
    participant_dates: set[tuple[int, object]],
    statuses: tuple[SwapStatus, ...],
    exclude_swap_id: int | None = None,
) -> ShiftSwap | None:
    """Return an active swap that already uses any of the affected user/date slots."""
    if not participant_dates:
        return None

    user_ids = {user_id for user_id, _ in participant_dates}
    dates = {d for _, d in participant_dates}
    query = db.query(ShiftSwap).filter(
        ShiftSwap.status.in_(statuses),
        (ShiftSwap.requester_id.in_(user_ids) | ShiftSwap.target_id.in_(user_ids)),
        (ShiftSwap.requester_date.in_(dates) | ShiftSwap.target_date.in_(dates)),
    )
    if exclude_swap_id is not None:
        query = query.filter(ShiftSwap.id != exclude_swap_id)

    for swap in query.all():
        existing_dates = swap_participant_dates(
            swap.requester_id, swap.target_id, swap.requester_date, swap.target_date
        )
        if participant_dates & existing_dates:
            return swap
    return None


def _rows_on_slots(db: Session, model, slots: set[tuple[int, date]]) -> bool:
    """True if model (user_id, date) has a row on any of the slots."""
    user_ids = {u for u, _ in slots}
    dates = {d for _, d in slots}
    rows = db.query(model.user_id, model.date).filter(model.user_id.in_(user_ids), model.date.in_(dates)).all()
    return any((u, d) in slots for u, d in rows)


def _slot_conflict(db: Session, requester: User, target: User, req_date, tgt_date, exclude_swap_id) -> str | None:
    slots = swap_participant_dates(requester.id, target.id, req_date, tgt_date)
    if find_active_swap_conflict(db, slots, (SwapStatus.PENDING, SwapStatus.ACCEPTED), exclude_swap_id):
        return SLOT_TAKEN
    if _rows_on_slots(db, ShiftOverride, slots):
        return "En manuell ändring finns redan för någon av personerna på dessa datum"
    if _rows_on_slots(db, Absence, slots):
        return "Någon av personerna är frånvarande ett av datumen"
    # Week-based vacation outranks overrides in the resolver, so a swap there would apply on one side only.
    positions = {requester.id: requester.rotation_person_id, target.id: target.rotation_person_id}
    vacation = {year: get_vacation_dates_for_year(year, session=db) for year in {req_date.year, tgt_date.year}}
    if any(d in vacation[d.year].get(positions[u], set()) for u, d in slots):
        return "Någon av personerna har semester ett av datumen"
    return None


def _code(day, rotation_person_id) -> str:
    result = determine_shift_for_date(day, start_week=rotation_person_id)
    return result[0].code if result and result[0] else "OFF"


def check_swap(
    db: Session,
    requester: User,
    target: User,
    req_date: date,
    tgt_date: date,
    *,
    exclude_swap_id: int | None = None,
) -> str | None:
    """Return the first Swedish reason the swap is not allowed, or None if it is.

    The requester gives their shift on req_date and takes the target's shift on tgt_date.
    """
    if requester.id == target.id:
        return "Kan inte byta pass med dig själv"

    today = get_today()
    if req_date <= today or tgt_date <= today:
        return "Kan bara byta framtida pass"

    duplicate = db.query(ShiftSwap).filter(
        ShiftSwap.requester_id == requester.id,
        ShiftSwap.target_id == target.id,
        ShiftSwap.requester_date == req_date,
        ShiftSwap.target_date == tgt_date,
        ShiftSwap.status == SwapStatus.PENDING,
    )
    if exclude_swap_id is not None:
        duplicate = duplicate.filter(ShiftSwap.id != exclude_swap_id)
    if duplicate.first():
        return "Byte redan föreslaget för dessa datum"

    reason = _slot_conflict(db, requester, target, req_date, tgt_date, exclude_swap_id)
    if reason:
        return reason

    req_pid = requester.rotation_person_id
    tgt_pid = target.rotation_person_id
    req_code = _code(req_date, req_pid)
    tgt_code = _code(tgt_date, tgt_pid)

    # ShiftOverride cannot hold on-call, so OC swaps stay manual.
    if "OC" in (req_code, tgt_code):
        return "OC-pass byts av admin"

    is_same_day = req_date == tgt_date
    if not is_same_day:
        # Cross-day swaps require both to be working
        if req_code == "OFF":
            return "Du jobbar inte det datumet"
        if tgt_code == "OFF":
            return "Kollegan jobbar inte det datumet"
    elif req_code == "OFF" and tgt_code == "OFF":
        return "Ingen av er jobbar den dagen"

    # Same-day mutual swap (both working) between identical shifts is pointless
    if is_same_day and req_code == tgt_code:
        return "Ni jobbar redan samma pass"

    # Same-day operations (mutual or one-way) skip rest checks.
    if is_same_day:
        return None

    # Rest is measured on the schedule after the swap: each side's given-away date is OFF.
    req_off = frozenset({req_date})
    tgt_off = frozenset({tgt_date})
    if not check_rest_ok(tgt_date, tgt_code, req_pid, session=db, off_dates=req_off):
        return "Bryter mot 11 timmars dygnsvila (du)"
    if not check_rest_ok(req_date, req_code, tgt_pid, session=db, off_dates=tgt_off):
        return "Bryter mot 11 timmars dygnsvila (kollegan)"
    if not check_weekly_rest_ok(tgt_date, tgt_code, req_pid, off_dates=req_off):
        return "Bryter mot 36 timmars veckovila (du)"
    if not check_weekly_rest_ok(req_date, req_code, tgt_pid, off_dates=tgt_off):
        return "Bryter mot 36 timmars veckovila (kollegan)"
    return None
