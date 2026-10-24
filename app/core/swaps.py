# app/core/swaps.py
"""Rules for shift swaps, shared by the compare screen endpoints, propose and accept."""

import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.core.schedule import build_week_data, calculate_shift_hours, get_shift_types
from app.core.schedule.period import oncall_window
from app.core.schedule.vacation import get_vacation_dates_for_year
from app.core.utils import get_today
from app.database.database import (
    Absence,
    OnCallOverride,
    OnCallOverrideType,
    ShiftOverride,
    ShiftSwap,
    SwapStatus,
    User,
    UserRole,
)

logger = logging.getLogger(__name__)

MIN_REST_HOURS = 11
MAX_PAIRS = 10
MIN_WEEKLY_REST_HOURS = 36

SWAPPABLE_CODES = frozenset({"N1", "N2", "N3", "OC", "OFF"})

OFF_DAY = ("OFF", None, None, (None, None, None, None))

SLOT_TAKEN = "Ett byte finns redan för någon av personerna på dessa datum"


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
    exclude_swap_ids: frozenset[int] = frozenset(),
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
    if exclude_swap_ids:
        query = query.filter(ShiftSwap.id.notin_(exclude_swap_ids))

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


def _slot_conflict(db: Session, requester: User, target: User, req_date, tgt_date, exclude_swap_ids) -> str | None:
    slots = swap_participant_dates(requester.id, target.id, req_date, tgt_date)
    if find_active_swap_conflict(db, slots, (SwapStatus.PENDING, SwapStatus.ACCEPTED), exclude_swap_ids):
        return SLOT_TAKEN
    # An ETC row is a custom labelled block, not a shift the other person could take.
    etc = db.query(ShiftOverride.id).filter(
        ShiftOverride.shift_code == "ETC",
        ((ShiftOverride.user_id == requester.id) & (ShiftOverride.date == req_date))
        | ((ShiftOverride.user_id == target.id) & (ShiftOverride.date == tgt_date)),
    )
    if etc.first():
        return "Passet är ett eget pass och kan inte bytas"
    if _rows_on_slots(db, Absence, slots):
        return "Någon av personerna är frånvarande ett av datumen"
    # Week-based vacation outranks overrides in the resolver, so a swap there would apply on one side only.
    positions = {requester.id: requester.rotation_person_id, target.id: target.rotation_person_id}
    vacation = {year: get_vacation_dates_for_year(year, session=db) for year in {req_date.year, tgt_date.year}}
    if any(d in vacation[d.year].get(positions[u], set()) for u, d in slots):
        return "Någon av personerna har semester ett av datumen"
    return None


def _custom_times(shift) -> tuple[str | None, str | None]:
    """The shift's own "HH:MM" window when it differs from its shift type's, else (None, None)."""
    default = next((s for s in get_shift_types() if s.code == shift.code), None)
    times = (shift.start_time, shift.end_time)
    return (None, None) if default and times == (default.start_time, default.end_time) else times


def _day(db: Session, user: User, day: date, cache: dict) -> tuple:
    """(code, start, end, times) for user on day, from build_week_data.

    start and end are the real datetimes of the day's work (custom and overtime clock
    times included), None when the day has none. times is (start, end, oc_start, oc_end):
    the custom shift window and the OnCallOverride window as "HH:MM", None for the
    shift type's own times and for whole-day on-call. Cached per user, position and ISO week.
    """
    pos = user.rotation_person_id
    year, week, _ = day.isocalendar()
    key = (user.id, pos, year, week)
    if key not in cache:
        monday = date.fromisocalendar(year, week, 1)
        oncall = {
            r.date: r
            for r in db.query(OnCallOverride).filter(
                OnCallOverride.user_id == user.id,
                OnCallOverride.date.between(monday, monday + timedelta(days=6)),
                OnCallOverride.override_type == OnCallOverrideType.ADD,
            )
        }
        cache[key] = {}
        for d in build_week_data(year, week, person_id=pos, session=db):
            shift = d.get("shift")
            code = shift.code if shift else "OFF"
            start, end = d.get("start"), d.get("end")
            if code == "OC" and not (start and end):
                # An on-call day can come back without clock times; it still blocks weekly rest.
                _, start, end = calculate_shift_hours(d["date"], code)
            times = _custom_times(shift) if shift and code != "OFF" else (None, None)
            row = oncall.get(d["date"]) if code == "OC" else None
            if row and oncall_window(d["date"], row) != oncall_window(d["date"]):
                times += (row.start_time, row.end_time)
            else:
                times += (None, None)
            cache[key][d["date"]] = (code, start, end, times)
    return cache[key].get(day, OFF_DAY)


def effective_code(db: Session, user: User, day: date, cache: dict | None = None) -> str:
    """The shift code the schedule shows for user on day, "OFF" when there is none.

    Read from build_week_data, so absence, vacation, ShiftOverride, swap_map, the
    rotation and OnCallOverride all count. Pass the same cache dict across calls to
    build each user's ISO week once.
    """
    return _day(db, user, day, {} if cache is None else cache)[0]


def assignment(db: Session, user: User, day: date, cache: dict | None = None) -> tuple:
    """Everything a swap hands over for user on day: (code, start, end, oc_start, oc_end).

    start and end are the custom shift window, oc_start and oc_end the on-call window,
    "HH:MM" strings or None for the defaults. See _day.
    """
    code, _, _, times = _day(db, user, day, {} if cache is None else cache)
    return (code, *times)


def _can_swap(user: User) -> bool:
    """Same definition as the colleague list: active and not an admin."""
    return user.is_active == 1 and user.role != UserRole.ADMIN


def check_swap(
    db: Session,
    requester: User,
    target: User,
    req_date: date,
    tgt_date: date,
    *,
    exclude_swap_ids: frozenset[int] = frozenset(),
    cache: dict | None = None,
) -> str | None:
    """Return the first Swedish reason the swap is not allowed, or None if it is.

    The requester gives their shift on req_date and takes the target's shift on tgt_date.
    Codes are the effective ones (see effective_code); cache is passed through to it.
    """
    if requester.id == target.id:
        return "Kan inte byta pass med dig själv"
    if not _can_swap(requester):
        return "Du kan inte byta pass"
    if not _can_swap(target):
        return "Användaren kan inte byta pass"

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
    if exclude_swap_ids:
        duplicate = duplicate.filter(ShiftSwap.id.notin_(exclude_swap_ids))
    if duplicate.first():
        return "Byte redan föreslaget för dessa datum"

    reason = _slot_conflict(db, requester, target, req_date, tgt_date, exclude_swap_ids)
    if reason:
        return reason

    cache = {} if cache is None else cache
    req_code = effective_code(db, requester, req_date, cache)
    tgt_code = effective_code(db, target, tgt_date, cache)
    # Called-in overtime ("OT") and other synthetic codes would be written as a ShiftOverride.
    if req_code not in SWAPPABLE_CODES or tgt_code not in SWAPPABLE_CODES:
        return "Passet kan inte bytas"

    is_same_day = req_date == tgt_date
    if not is_same_day:
        # Cross-day swaps require both to be working
        if req_code == "OFF":
            return "Du jobbar inte det datumet"
        if tgt_code == "OFF":
            return "Kollegan jobbar inte det datumet"
        # Shifts are exchanged on both dates, so each side must be free on the other's date.
        # This is also what lets the rest checks below treat the given-away date as OFF.
        if effective_code(db, requester, tgt_date, cache) != "OFF":
            return "Du jobbar redan det datumet"
        if effective_code(db, target, req_date, cache) != "OFF":
            return "Kollegan jobbar redan det datumet"
    elif req_code == "OFF" and tgt_code == "OFF":
        return "Ingen av er jobbar den dagen"

    # Same-day mutual swap (both working) between identical shifts is pointless
    if is_same_day and req_code == tgt_code:
        return "Ni jobbar redan samma pass"
    return None


def validate_proposal(
    db: Session, requester: User, target: User, pairs: list[tuple[date, date]], cache: dict | None = None
) -> str | None:
    """Return the first Swedish reason a proposal of (req_date, tgt_date) pairs is refused, or None.

    Every pair must pass check_swap, and no two pairs may touch the same user/date slot,
    so accepting the group can never write two override rows on one slot.
    """
    if not pairs:
        return "Inga byten valda"
    if len(pairs) > MAX_PAIRS:
        return f"Högst {MAX_PAIRS} byten per förslag"
    cache = {} if cache is None else cache
    for n, (req_date, tgt_date) in enumerate(pairs, start=1):
        reason = check_swap(db, requester, target, req_date, tgt_date, cache=cache)
        if reason:
            return f"Byte {n}: {reason}"
    used: dict[tuple[int, date], int] = {}
    for n, (req_date, tgt_date) in enumerate(pairs, start=1):
        slots = swap_participant_dates(requester.id, target.id, req_date, tgt_date)
        clash = next((used[slot] for slot in slots if slot in used), None)
        if clash:
            return f"Byte {n}: samma person och datum som byte {clash}"
        used.update(dict.fromkeys(slots, n))
    return None


def _days(db: Session, user: User, first: date, last: date, cache: dict) -> dict:
    """{date: (code, start, end)} for every day from first to last."""
    days = {}
    for n in range((last - first).days + 1):
        d = first + timedelta(days=n)
        days[d] = _day(db, user, d, cache)
    return days


def _hours(delta: timedelta) -> float:
    return delta.total_seconds() / 3600


def _work(days: dict, with_oc: bool) -> list[tuple[datetime, datetime, date]]:
    """Sorted (start, end, day) of the work in days. On call (OC) blocks weekly rest only."""
    return sorted((s, e, d) for d, (code, s, e, _) in days.items() if s and e and (with_oc or code != "OC"))


def _shortest_daily_rest(days: dict, dates: set[date]) -> float | None:
    """Shortest rest between consecutive work blocks where either block is on one of dates."""
    work = _work(days, with_oc=False)
    gaps = [_hours(b[0] - a[1]) for a, b in zip(work, work[1:], strict=False) if a[2] in dates or b[2] in dates]
    return min(gaps, default=None)


def _longest_weekly_rest(days: dict, monday: date) -> float:
    """Longest unbroken rest inside the ISO week from monday 00:00 to the next Monday 00:00."""
    start = datetime.combine(monday, time())
    end = start + timedelta(days=7)
    longest, free_from = 0.0, start
    for s, e, _ in _work(days, with_oc=True):
        s, e = max(s, start), min(e, end)
        if s >= e:
            continue
        longest = max(longest, _hours(s - free_from))
        free_from = max(free_from, e)
    return max(longest, _hours(end - free_from))


def proposal_warnings(
    db: Session, requester: User, target: User, pairs: list[tuple[date, date]], cache: dict | None = None
) -> list[list[str]]:
    """Swedish rest warnings for each (req_date, tgt_date) pair, in pair order. Never raises.

    Rest is measured on both schedules with every pair applied: on each pair's dates the
    two people exchange their effective days, clock times included. A warning is given
    only where the proposal introduces or worsens a breach: 11 h between work blocks
    next to a pair's dates, and 36 h unbroken rest inside each ISO week (Monday to
    Monday) that holds one of the pair's dates.
    """
    try:
        return _proposal_warnings(db, requester, target, pairs, {} if cache is None else cache)
    except Exception:
        logger.exception("proposal_warnings failed for %s -> %s, %s", requester.id, target.id, pairs)
        return [[] for _ in pairs]


def _proposal_warnings(db, requester, target, pairs, cache) -> list[list[str]]:
    if not pairs:
        return []
    pair_dates = [{rd, td} for rd, td in pairs]
    mondays = {d - timedelta(days=d.weekday()) for dates in pair_dates for d in dates}
    # The Sunday before reaches a night shift into Monday; the Monday after is the last neighbour.
    first, last = min(mondays) - timedelta(days=1), max(mondays) + timedelta(days=7)
    people = {"du": requester, "kollegan": target}
    before = {who: _days(db, user, first, last, cache) for who, user in people.items()}
    after = {who: dict(days) for who, days in before.items()}
    for dates in pair_dates:
        for d in dates:
            after["du"][d], after["kollegan"][d] = after["kollegan"][d], after["du"][d]

    short_weeks = set()
    for who in people:
        for monday in mondays:
            new = _longest_weekly_rest(after[who], monday)
            if new < MIN_WEEKLY_REST_HOURS and new < _longest_weekly_rest(before[who], monday):
                short_weeks.add((who, monday))

    result = []
    for dates in pair_dates:
        warnings = []
        for who in people:
            new = _shortest_daily_rest(after[who], dates)
            old = _shortest_daily_rest(before[who], dates)
            if new is not None and new < MIN_REST_HOURS and (old is None or new < old):
                warnings.append(f"Under {MIN_REST_HOURS} timmars dygnsvila ({who})")
        weeks = {d - timedelta(days=d.weekday()) for d in dates}
        for who in people:
            if any((who, monday) in short_weeks for monday in weeks):
                warnings.append(f"Under {MIN_WEEKLY_REST_HOURS} timmars veckovila ({who})")
        result.append(warnings)
    return result
