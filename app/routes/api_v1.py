"""External REST API v1 for integrations (e.g. Home Assistant)."""

import datetime

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.auth import get_admin_api_user, get_api_user
from app.core.schedule.core import determine_shift_for_date, get_shift_types
from app.core.schedule.ob import compute_day_ob_pay, get_combined_rules_for_year
from app.core.schedule.period import generate_period_data
from app.core.utils import APP_TIMEZONE, get_today
from app.database.database import (
    Absence,
    OvertimeShift,
    User,
    UserRole,
    get_db,
)

router = APIRouter(tags=["api-v1"])


def _can_see_salary(current_user: User, target_user: User) -> bool:
    return current_user.id == target_user.id or current_user.role == UserRole.ADMIN


def _get_user_or_404(user_id: int, db: Session) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="Användare hittades inte")
    return user


def _month_range(year: int, month: int) -> tuple[datetime.date, datetime.date]:
    start = datetime.date(year, month, 1)
    end = (datetime.date(year + 1, 1, 1) if month == 12 else datetime.date(year, month + 1, 1)) - datetime.timedelta(
        days=1
    )
    return start, end


def _absent_ids_by_date(start: datetime.date, end: datetime.date, db: Session) -> dict[datetime.date, set[int]]:
    rows = db.query(Absence.user_id, Absence.date).filter(Absence.date >= start, Absence.date <= end).all()
    result: dict[datetime.date, set[int]] = {}
    for uid, d in rows:
        result.setdefault(d, set()).add(uid)
    return result


def _overtime_by_date(
    start: datetime.date, end: datetime.date, db: Session
) -> dict[datetime.date, dict[int, datetime.time]]:
    """Returns {date: {user_id: end_time}} for full (non-extension) overtime shifts."""
    rows = (
        db.query(OvertimeShift.user_id, OvertimeShift.date, OvertimeShift.end_time)
        .filter(OvertimeShift.date >= start, OvertimeShift.date <= end, OvertimeShift.is_extension.is_(False))
        .all()
    )
    result: dict[datetime.date, dict[int, datetime.time]] = {}
    for uid, d, et in rows:
        result.setdefault(d, {})[uid] = et
    return result


def _is_overnight(shift) -> bool:
    """Returns True when the shift ends on the next calendar day (end_time <= start_time)."""
    if not shift or not shift.start_time or not shift.end_time:
        return False
    return shift.end_time <= shift.start_time


def _shift_to_dict(shift) -> dict:
    return {
        "code": shift.code,
        "label": shift.label,
        "start_time": shift.start_time,
        "end_time": shift.end_time,
        "color": shift.color,
        "overnight": _is_overnight(shift),
    }


def _find_shift_for_overtime(ot_end: datetime.time) -> tuple[str, str]:
    """Match overtime end time to a named shift; falls back to 'OT'/'Overtime'."""
    ot_end_str = ot_end.strftime("%H:%M")
    for shift in get_shift_types():
        if shift.end_time == ot_end_str and shift.code not in ("OFF", "OC"):
            label = shift.label or shift.code
            return f"OT-{shift.code}", f"Övertid ({label})"
    return "OT", "Övertid"


def _build_coworkers(
    user_id: int,
    date: datetime.date,
    all_users: list[User],
    absent_ids: set[int],
    overtime_map: dict[int, datetime.time],
) -> list[dict]:
    """Who else is working that day.

    NOTE: this is the one place left in the module that reads the rotation directly
    instead of the canonical path, so a co-worker's swap or shift override is not
    reflected in their shift_code (their absences are, via absent_ids). The canonical
    source would be generate_period_data(person_id=None), but that resolves
    PersonHistory per person and day: measured at ~52 queries per calendar day
    against ~5 for the single-person call, which turns /schedule/year from ~2.5k
    into ~21k queries. Switch this over once period.py batches those lookups.
    """
    result = []
    for u in all_users:
        if u.id == user_id or u.id in absent_ids or u.role == UserRole.ADMIN:
            continue
        if u.id in overtime_map:
            code, label = _find_shift_for_overtime(overtime_map[u.id])
            result.append({"id": u.id, "name": u.name, "shift_code": code, "shift_label": label})
            continue
        shift, _ = determine_shift_for_date(date, u.rotation_person_id)
        if shift and shift.code != "OFF":
            result.append({"id": u.id, "name": u.name, "shift_code": shift.code, "shift_label": shift.label})
    return result


def _day_status_and_shift(canonical: dict, absence, overtime) -> tuple[str, object]:
    """Map a canonical day to the API's (status, shift) pair.

    The API's `shift` field has always meant "the shift this person was assigned",
    with absence/vacation reported separately in `status`; the canonical day instead
    replaces `shift` with the absence, vacation or overtime shift. So the assigned
    shift is taken from `original_shift` on those days, and from the fully resolved
    `shift` (rotation + swap + override + on-call override) on every other day.
    """
    shift = canonical.get("shift")
    assigned = canonical.get("original_shift") or shift

    def _status(s) -> str:
        if s is None:
            return "unknown"
        return "off" if s.code == "OFF" else "working"

    if absence is not None:
        return absence.absence_type.value.lower(), assigned
    if canonical.get("parental_leave"):
        return "parental", assigned
    if shift is not None and shift.code == "SEM":
        return "vacation", assigned
    if overtime is not None and not overtime.is_extension:
        # Canonical swaps in the OT shift type; the API reports overtime in its own
        # block and keeps the underlying shift here.
        return _status(assigned), assigned
    return _status(shift), shift


def _build_day(
    canonical: dict,
    date: datetime.date,
    user: User,
    db: Session,
    absence,
    overtime,
    include_salary: bool,
    coworkers: list[dict] | None,
) -> dict:
    status, shift = _day_status_and_shift(canonical, absence, overtime)

    result = {
        "date": date.isoformat(),
        "status": status,
        "shift": _shift_to_dict(shift) if shift else None,
        "rotation_week": canonical.get("rotation_week"),
        # An absence outranks overtime in the response, as it always has.
        "overtime": None
        if (absence is not None or overtime is None)
        else {
            "start_time": overtime.start_time.strftime("%H:%M"),
            "end_time": overtime.end_time.strftime("%H:%M"),
            "hours": overtime.hours,
            "is_extension": overtime.is_extension,
        },
        "partial_day": absence.left_at if absence is not None else None,
    }
    if absence is not None:
        result["arrived_late"] = absence.arrived_at
    if include_salary:
        result["ob_pay"], result["ob_total"] = _day_ob_pay(canonical, date, user, db)
    if coworkers is not None:
        result["coworkers"] = coworkers
    return result


def _day_ob_pay(canonical: dict, date: datetime.date, user: User, db: Session) -> tuple[dict | None, float]:
    """OB pay for a canonical day, through the shared gate used by the day view."""
    c_shift = canonical.get("shift")
    has_ob = bool(canonical.get("ob_hours_override")) or bool(
        c_shift and c_shift.code not in ("OFF", "OC", "OT") and canonical.get("start") and canonical.get("end")
    )
    if not has_ob:
        return None, 0.0

    from app.core.schedule.wages import get_effective_monthly_wage

    monthly_wage = get_effective_monthly_wage(db, user.id, user.wage, effective_date=date)
    _, ob_pay, _ = compute_day_ob_pay(
        canonical,
        get_combined_rules_for_year(date.year),
        monthly_wage,
        (user.custom_rates or {}).get("ob"),
    )
    return {k: round(v, 2) for k, v in ob_pay.items() if v > 0}, round(sum(ob_pay.values()), 2)


def _build_period(
    target: User,
    start: datetime.date,
    end: datetime.date,
    db: Session,
    include_salary: bool,
    with_coworkers: bool = True,
) -> tuple[list[dict], float]:
    """Build day-status list for a range; returns (days, ob_total_sum).

    INVARIANT (issue #206, same rule as app/routes/schedule_personal.py): shift
    resolution comes exclusively from generate_period_data, so shift overrides,
    swaps, on-call overrides, day pay overrides, linked substitutes and employment
    masking reach this API automatically. The Absence and OvertimeShift rows read
    below only decorate the response (status text, partial-day times, the overtime
    block); do not reintroduce shadow calculations on top of them. A new override
    layer belongs in period.py, where it reaches every view at once.
    """
    canonical_days = {
        day["date"]: day for day in generate_period_data(start, end, person_id=target.rotation_person_id, session=db)
    }
    absences = {
        a.date: a
        for a in db.query(Absence).filter(Absence.user_id == target.id, Absence.date.between(start, end)).all()
    }
    overtimes = {
        o.date: o
        for o in db.query(OvertimeShift)
        .filter(OvertimeShift.user_id == target.id, OvertimeShift.date.between(start, end))
        .all()
    }

    all_users: list[User] = []
    absent_by_date: dict[datetime.date, set[int]] = {}
    overtime_by_date: dict[datetime.date, dict[int, datetime.time]] = {}
    if with_coworkers:
        all_users = db.query(User).filter(User.is_active == 1).all()
        absent_by_date = _absent_ids_by_date(start, end, db)
        overtime_by_date = _overtime_by_date(start, end, db)

    days = []
    ob_sum = 0.0
    d = start
    while d <= end:
        absence = absences.get(d)
        coworkers = None
        if with_coworkers:
            # The viewed user counts as absent for co-worker matching too.
            absent_ids = absent_by_date.get(d, set()) | ({target.id} if absence else set())
            coworkers = _build_coworkers(target.id, d, all_users, absent_ids, overtime_by_date.get(d, {}))
        day = _build_day(
            canonical_days.get(d, {}),
            d,
            target,
            db,
            absence,
            overtimes.get(d),
            include_salary,
            coworkers,
        )
        if include_salary:
            ob_sum += day.get("ob_total") or 0.0
        days.append(day)
        d += datetime.timedelta(days=1)
    return days, round(ob_sum, 2)


def _active_overnight_shift(day: dict, current_time: datetime.time) -> dict | None:
    """The given day's shift, when it crosses midnight and is still running at current_time."""
    shift = day["shift"]
    if day["status"] != "working" or not shift or shift["code"] == "OC" or not shift["overnight"]:
        return None
    if current_time >= datetime.time.fromisoformat(shift["end_time"]):
        return None
    return {"date": day["date"], "shift": shift, "rotation_week": day["rotation_week"]}


def _build_day_status(
    user: User,
    date: datetime.date,
    db: Session,
    include_salary: bool = False,
    with_coworkers: bool = False,
) -> dict:
    """Single-day status; a one-day slice of the canonical period path."""
    days, _ = _build_period(user, date, date, db, include_salary, with_coworkers=with_coworkers)
    return days[0]


# ── Own user shortcut ────────────────────────────────────────────────────────


@router.get("/me")
async def get_me(current_user: User = Depends(get_api_user)):
    """Basic info about the authenticated user."""
    return {
        "id": current_user.id,
        "name": current_user.name,
        "username": current_user.username,
        "role": current_user.role.value,
        "is_active": bool(current_user.is_active),
        "rotation_person_id": current_user.rotation_person_id,
    }


@router.get("/shifts")
async def get_shifts(current_user: User = Depends(get_api_user)):
    """All shift type definitions (code, label, times, color), including synthetic OT-N1/N2/N3 variants."""
    from app.core.constants import WORK_SHIFT_CODES

    shift_types = get_shift_types()
    result = [_shift_to_dict(s) for s in shift_types]

    ot_shift = next((s for s in shift_types if s.code == "OT"), None)
    ot_color = ot_shift.color if ot_shift else "#ff9800"

    for s in shift_types:
        if s.code in WORK_SHIFT_CODES:
            result.append(
                {
                    "code": f"OT-{s.code}",
                    "label": f"Övertid ({s.label})",
                    "start_time": None,
                    "end_time": s.end_time,
                    "color": ot_color,
                    "overnight": _is_overnight(s),
                }
            )

    return result


# ── Per-user endpoints ───────────────────────────────────────────────────────


@router.get("/users/{user_id}/status")
async def get_user_status(
    user_id: int,
    at_date: str | None = Query(None, alias="date", description="Simulate date (YYYY-MM-DD)"),
    at_time: str | None = Query(None, alias="time", description="Simulate time (HH:MM)"),
    current_user: User = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """Status for a given user. Defaults to now; pass ?date=YYYY-MM-DD&time=HH:MM to simulate."""
    target = _get_user_or_404(user_id, db)
    include_salary = _can_see_salary(current_user, target)
    if at_date:
        try:
            today = datetime.date.fromisoformat(at_date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Ogiltigt datumformat, använd YYYY-MM-DD") from e
        try:
            current_time = datetime.time.fromisoformat(at_time) if at_time else datetime.time(0, 0)
        except ValueError:
            raise HTTPException(status_code=400, detail="Ogiltigt tidsformat, använd HH:MM") from None
    else:
        now = datetime.datetime.now(APP_TIMEZONE)
        today = now.date()
        current_time = now.time()
    yesterday = today - datetime.timedelta(days=1)
    days, _ = _build_period(target, yesterday, today, db, include_salary)
    previous, result = days
    # Check for an ongoing overnight shift from the previous day.
    active = _active_overnight_shift(previous, current_time)
    if active is not None:
        result["currently_active_shift"] = active
    return result


@router.get("/users/{user_id}/schedule/today")
async def get_user_schedule_today(
    user_id: int,
    at_date: str | None = Query(None, alias="date", description="Simulate date (YYYY-MM-DD)"),
    current_user: User = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """Schedule for today including co-workers. Pass ?date=YYYY-MM-DD to simulate another day."""
    target = _get_user_or_404(user_id, db)
    include_salary = _can_see_salary(current_user, target)
    if at_date:
        try:
            today = datetime.date.fromisoformat(at_date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Ogiltigt datumformat, använd YYYY-MM-DD") from e
    else:
        today = get_today()
    return _build_day_status(target, today, db, include_salary=include_salary, with_coworkers=True)


@router.get("/users/{user_id}/schedule/month")
async def get_user_schedule_month(
    user_id: int,
    current_user: User = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """Schedule for the current calendar month with co-workers per day."""
    target = _get_user_or_404(user_id, db)
    include_salary = _can_see_salary(current_user, target)
    today = get_today()
    start, end = _month_range(today.year, today.month)
    days, ob_total = _build_period(target, start, end, db, include_salary)
    result: dict = {"month": today.month, "year": today.year, "days": days}
    if include_salary:
        result["wage"] = target.wage
        result["ob_total"] = ob_total
    return result


@router.get("/users/{user_id}/schedule/year")
async def get_user_schedule_year(
    user_id: int,
    year: int | None = None,
    current_user: User = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """Schedule for a full year (?year=YYYY, defaults to current year)."""
    target = _get_user_or_404(user_id, db)
    include_salary = _can_see_salary(current_user, target)
    if year is None:
        year = get_today().year
    start = datetime.date(year, 1, 1)
    end = datetime.date(year, 12, 31)
    days, ob_total = _build_period(target, start, end, db, include_salary)
    result: dict = {"year": year, "days": days}
    if include_salary:
        result["wage"] = target.wage
        result["ob_total"] = ob_total
    return result


@router.get("/users/{user_id}/schedule/week/{date}")
async def get_user_schedule_week(
    user_id: int,
    date: str,
    current_user: User = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """Schedule for the ISO week containing the given date (YYYY-MM-DD)."""
    target = _get_user_or_404(user_id, db)
    try:
        target_date = datetime.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ogiltigt datumformat, använd YYYY-MM-DD") from None
    monday = target_date - datetime.timedelta(days=target_date.weekday())
    sunday = monday + datetime.timedelta(days=6)
    include_salary = _can_see_salary(current_user, target)
    days, _ = _build_period(target, monday, sunday, db, include_salary)
    return {
        "week": target_date.isocalendar()[1],
        "year": target_date.isocalendar()[0],
        "days": days,
    }


@router.get("/users/{user_id}/schedule")
async def get_user_schedule_range(
    user_id: int,
    from_date: str,
    to_date: str,
    current_user: User = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """Schedule for a date range (?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD, max 70 days)."""
    target = _get_user_or_404(user_id, db)
    try:
        start = datetime.date.fromisoformat(from_date)
        end = datetime.date.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ogiltigt datumformat, använd YYYY-MM-DD") from None
    if end < start:
        raise HTTPException(status_code=400, detail="to_date måste vara efter from_date")
    if (end - start).days >= 70:
        raise HTTPException(status_code=400, detail="Max 70 dagar per anrop")
    include_salary = _can_see_salary(current_user, target)
    days, _ = _build_period(target, start, end, db, include_salary)
    return {"days": days}


@router.get("/users/{user_id}/schedule/{date}")
async def get_user_schedule_date(
    user_id: int,
    date: str,
    current_user: User = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """Schedule for a specific date (YYYY-MM-DD) including co-workers."""
    target = _get_user_or_404(user_id, db)
    try:
        target_date = datetime.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ogiltigt datumformat, använd YYYY-MM-DD") from None
    include_salary = _can_see_salary(current_user, target)
    return _build_day_status(target, target_date, db, include_salary=include_salary, with_coworkers=True)


# ── Users list ──────────────────────────────────────────────────────────────


@router.get("/users")
async def list_users(
    current_user: User = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """List all active users (id, name)."""
    users = db.query(User).filter(User.is_active == 1).order_by(User.name).all()
    return [{"id": u.id, "name": u.name} for u in users]


# ── Pay summary ──────────────────────────────────────────────────────────────


@router.get("/users/{user_id}/pay/month")
async def get_user_pay_month(
    user_id: int,
    year: int | None = None,
    month: int | None = None,
    current_user: User = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """Full pay summary for a month (?year=YYYY&month=M, defaults to current month). Own user or admin only."""
    target = _get_user_or_404(user_id, db)
    if not _can_see_salary(current_user, target):
        raise HTTPException(status_code=403, detail="Åtkomst nekad")
    today = get_today()
    year = year or today.year
    month = month or today.month
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Ogiltigt månadsvärde (1-12)")

    from app.core.schedule.summary import summarize_month_for_person

    summary = summarize_month_for_person(
        year,
        month,
        target.rotation_person_id,
        session=db,
        wage_user_id=target.id,
        payment_year=year,
    )
    summary.pop("days", None)
    summary.pop("absence_details", None)
    return summary


# ── Vacation balance ─────────────────────────────────────────────────────────


@router.get("/users/{user_id}/vacation/balance")
async def get_user_vacation_balance(
    user_id: int,
    year: int | None = None,
    current_user: User = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """Vacation balance for a year (?year=YYYY, defaults to current year). Own user or admin only."""
    target = _get_user_or_404(user_id, db)
    if not _can_see_salary(current_user, target):
        raise HTTPException(status_code=403, detail="Åtkomst nekad")
    year = year or get_today().year

    from app.core.schedule.vacation import calculate_vacation_balance

    balance = calculate_vacation_balance(target, year, db)

    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    return {
        "year": year,
        "entitled_days": balance["entitled_days"],
        "saved_from_previous": balance["saved_from_previous"],
        "total_available": balance["total_available"],
        "used_days": balance["used_days"],
        "remaining_days": balance["remaining_days"],
        "year_start": _iso(balance["year_start"]),
        "year_end": _iso(balance["year_end"]),
        "is_first_year": balance["is_first_year"],
        "projection": balance.get("projection"),
        "closed": balance.get("closed"),
    }


# ── Absences ─────────────────────────────────────────────────────────────────


@router.get("/users/{user_id}/absences")
async def get_user_absences(
    user_id: int,
    year: int | None = None,
    current_user: User = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """List absences for a user (?year=YYYY, defaults to current year). Own user or admin only."""
    target = _get_user_or_404(user_id, db)
    if not _can_see_salary(current_user, target):
        raise HTTPException(status_code=403, detail="Åtkomst nekad")
    year = year or get_today().year
    start = datetime.date(year, 1, 1)
    end = datetime.date(year, 12, 31)
    rows = (
        db.query(Absence)
        .filter(Absence.user_id == target.id, Absence.date >= start, Absence.date <= end)
        .order_by(Absence.date)
        .all()
    )
    return [
        {
            "id": a.id,
            "date": a.date.isoformat(),
            "type": a.absence_type.value,
            "partial_day": a.left_at,
            "arrived_late": a.arrived_at,
        }
        for a in rows
    ]


# ── Next shift ───────────────────────────────────────────────────────────────


@router.get("/users/{user_id}/next-shift")
async def get_user_next_shift(
    user_id: int,
    at_date: str | None = Query(None, alias="date", description="Simulate from date (YYYY-MM-DD)"),
    at_time: str | None = Query(None, alias="time", description="Simulate from time (HH:MM)"),
    current_user: User = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """Next working day for a user. Optionally pass ?date=YYYY-MM-DD&time=HH:MM to simulate."""
    target = _get_user_or_404(user_id, db)
    if at_date:
        try:
            today = datetime.date.fromisoformat(at_date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Ogiltigt datumformat, använd YYYY-MM-DD") from e
        try:
            current_time = datetime.time.fromisoformat(at_time) if at_time else datetime.time(0, 0)
        except ValueError:
            raise HTTPException(status_code=400, detail="Ogiltigt tidsformat, använd HH:MM") from None
    else:
        now = datetime.datetime.now(APP_TIMEZONE)
        today = now.date()
        current_time = now.time()

    yesterday = today - datetime.timedelta(days=1)
    days, _ = _build_period(
        target, yesterday, today + datetime.timedelta(days=59), db, include_salary=False, with_coworkers=False
    )
    # Check if there is an ongoing overnight shift from yesterday still running.
    currently_active = _active_overnight_shift(days[0], current_time)

    for offset, day in enumerate(days[1:]):
        # Absence, vacation and parental days never report as "working".
        if day["status"] != "working" or not day["shift"] or not day["shift"]["start_time"]:
            continue
        if offset == 0 and current_time >= datetime.time.fromisoformat(day["shift"]["start_time"]):
            continue
        result = {
            "date": day["date"],
            "days_from_today": offset,
            "shift": day["shift"],
            "rotation_week": day["rotation_week"],
        }
        if currently_active is not None:
            result["currently_active_shift"] = currently_active
        return result

    raise HTTPException(status_code=404, detail="Inget kommande pass hittades inom 60 dagar")


# ── Admin router ─────────────────────────────────────────────────────────────

admin_router = APIRouter(tags=["admin"])


@admin_router.get("/team/today")
async def get_team_today(
    admin_user: User = Depends(get_admin_api_user),
    db: Session = Depends(get_db),
):
    """Status for all active users today."""
    users = db.query(User).filter(User.is_active == 1).all()
    today = get_today()
    return {
        "date": today.isoformat(),
        "team": [{"id": u.id, "name": u.name, **_build_day_status(u, today, db)} for u in users],
    }


@admin_router.get("/team/schedule")
async def get_team_schedule_range(
    from_date: str,
    to_date: str,
    admin_user: User = Depends(get_admin_api_user),
    db: Session = Depends(get_db),
):
    """Schedule for all active users over a date range (max 14 days)."""
    try:
        start = datetime.date.fromisoformat(from_date)
        end = datetime.date.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ogiltigt datumformat, använd YYYY-MM-DD") from None
    if end < start:
        raise HTTPException(status_code=400, detail="to_date måste vara efter from_date")
    if (end - start).days > 14:
        raise HTTPException(status_code=400, detail="Max 14 dagar per anrop för teamvy")
    users = db.query(User).filter(User.is_active == 1).all()
    result = []
    for u in users:
        days, _ = _build_period(u, start, end, db, include_salary=False)
        result.append({"id": u.id, "name": u.name, "days": days})
    return {"from_date": start.isoformat(), "to_date": end.isoformat(), "team": result}


# ── Sub-app factories ─────────────────────────────────────────────────────────

_DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}


async def _admin_cookie_check(request, call_next):
    """Middleware: requires an authenticated admin session for docs pages."""
    if request.url.path in _DOCS_PATHS:
        from app.auth.auth import get_current_user_from_cookie
        from app.database.database import SessionLocal, UserRole

        db = SessionLocal()
        try:
            user = await get_current_user_from_cookie(request, db)
        finally:
            db.close()

        if not user or user.role != UserRole.ADMIN:
            from starlette.responses import RedirectResponse

            return RedirectResponse("/login")

    return await call_next(request)


def _add_bearer_security(api: FastAPI, server_prefix: str) -> None:
    """Inject BearerAuth security scheme and correct server prefix into the sub-app's OpenAPI schema."""
    from fastapi.openapi.utils import get_openapi

    def custom_openapi():
        if api.openapi_schema:
            return api.openapi_schema
        schema = get_openapi(
            title=api.title,
            version=api.version,
            description=api.description,
            routes=api.routes,
        )
        schema["servers"] = [{"url": server_prefix}]
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
        }
        for path in schema.get("paths", {}).values():
            for operation in path.values():
                operation.setdefault("security", [{"BearerAuth": []}])
        api.openapi_schema = schema
        return schema

    api.openapi = custom_openapi


def create_api_app() -> FastAPI:
    api = FastAPI(
        title="Periodical API",
        description="REST API för schema, lön och frånvaro. Autentisera med `Authorization: Bearer <api-nyckel>`.",
        version="1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    api.include_router(router)
    _add_bearer_security(api, server_prefix="/api/v1")
    return api


def create_admin_api_app() -> FastAPI:
    api = FastAPI(
        title="Periodical Admin API",
        description="Admin-endpoints för teamöversikter. Kräver admin API-nyckel.",
        version="1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    api.include_router(admin_router)
    _add_bearer_security(api, server_prefix="/api/v1/admin")
    return api
