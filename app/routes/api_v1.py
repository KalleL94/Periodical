"""External REST API v1 for integrations (e.g. Home Assistant)."""

import datetime

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from app.auth.auth import get_admin_api_user, get_api_user
from app.core.schedule.core import calculate_shift_hours, determine_shift_for_date
from app.core.schedule.ob import calculate_ob_pay, get_combined_rules_for_year
from app.core.utils import get_today
from app.database.database import Absence, OvertimeShift, User, UserRole, get_db

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


def _build_coworkers(user_id: int, date: datetime.date, all_users: list[User], absent_ids: set[int]) -> list[dict]:
    result = []
    for u in all_users:
        if u.id == user_id or u.id in absent_ids:
            continue
        shift, _ = determine_shift_for_date(date, u.rotation_person_id)
        if shift and shift.code not in ("OFF",):
            result.append({"id": u.id, "name": u.name, "shift_code": shift.code, "shift_label": shift.label})
    return result


def _build_day_status(
    user: User,
    date: datetime.date,
    db: Session,
    include_salary: bool = False,
    all_users: list[User] | None = None,
    absent_ids: set[int] | None = None,
) -> dict:
    absence = db.query(Absence).filter(Absence.user_id == user.id, Absence.date == date).first()
    overtime = db.query(OvertimeShift).filter(OvertimeShift.user_id == user.id, OvertimeShift.date == date).first()
    shift, rotation_week = determine_shift_for_date(date, user.rotation_person_id)

    coworkers = None
    if all_users is not None:
        effective_absent = (
            absent_ids
            if absent_ids is not None
            else {row[0] for row in db.query(Absence.user_id).filter(Absence.date == date).all()}
        )
        # Include the current user as absent if they have an absence
        if absence:
            effective_absent = effective_absent | {user.id}
        coworkers = _build_coworkers(user.id, date, all_users, effective_absent)

    if absence:
        result = {
            "date": date.isoformat(),
            "status": absence.absence_type.value.lower(),
            "shift": None,
            "rotation_week": rotation_week,
            "overtime": None,
            "partial_day": absence.left_at,
        }
        if include_salary:
            result["ob_pay"] = None
            result["ob_total"] = 0.0
        if coworkers is not None:
            result["coworkers"] = coworkers
        return result

    ob_pay_data = None
    ob_total = 0.0

    if shift and shift.code not in ("OFF", "OC") and include_salary:
        is_full_ot = overtime and not overtime.is_extension
        if not is_full_ot:
            _, start_dt, end_dt = calculate_shift_hours(date, shift)
            if start_dt and end_dt:
                rules = get_combined_rules_for_year(date.year)
                rate_overrides = (user.custom_rates or {}).get("ob")
                ob_pay_raw = calculate_ob_pay(start_dt, end_dt, rules, user.wage, rate_overrides)
                ob_total = round(sum(ob_pay_raw.values()), 2)
                ob_pay_data = {k: round(v, 2) for k, v in ob_pay_raw.items() if v > 0}

    shift_data = None
    if shift:
        shift_data = {
            "code": shift.code,
            "label": shift.label,
            "start_time": shift.start_time,
            "end_time": shift.end_time,
            "color": shift.color,
        }

    overtime_data = None
    if overtime:
        overtime_data = {
            "start_time": overtime.start_time.strftime("%H:%M"),
            "end_time": overtime.end_time.strftime("%H:%M"),
            "hours": overtime.hours,
            "is_extension": overtime.is_extension,
        }

    status = "off" if (shift and shift.code == "OFF") else ("working" if shift else "unknown")

    result = {
        "date": date.isoformat(),
        "status": status,
        "shift": shift_data,
        "rotation_week": rotation_week,
        "overtime": overtime_data,
        "partial_day": None,
    }
    if include_salary:
        result["ob_pay"] = ob_pay_data
        result["ob_total"] = ob_total
    if coworkers is not None:
        result["coworkers"] = coworkers
    return result


def _build_period(
    target: User,
    start: datetime.date,
    end: datetime.date,
    db: Session,
    include_salary: bool,
) -> tuple[list[dict], float]:
    """Build day-status list for a range; returns (days, ob_total_sum)."""
    all_users = db.query(User).filter(User.is_active == 1).all()
    absent_by_date = _absent_ids_by_date(start, end, db)
    days = []
    ob_sum = 0.0
    d = start
    while d <= end:
        day = _build_day_status(
            target,
            d,
            db,
            include_salary=include_salary,
            all_users=all_users,
            absent_ids=absent_by_date.get(d, set()),
        )
        if include_salary:
            ob_sum += day.get("ob_total") or 0.0
        days.append(day)
        d += datetime.timedelta(days=1)
    return days, round(ob_sum, 2)


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


# ── Per-user endpoints ───────────────────────────────────────────────────────


@router.get("/users/{user_id}/status")
async def get_user_status(
    user_id: int,
    current_user: User = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """Status for today for a given user. Includes co-workers and OB if own user or admin."""
    target = _get_user_or_404(user_id, db)
    include_salary = _can_see_salary(current_user, target)
    all_users = db.query(User).filter(User.is_active == 1).all()
    today = get_today()
    absent_ids = {row[0] for row in db.query(Absence.user_id).filter(Absence.date == today).all()}
    return _build_day_status(
        target, today, db, include_salary=include_salary, all_users=all_users, absent_ids=absent_ids
    )


@router.get("/users/{user_id}/schedule/today")
async def get_user_schedule_today(
    user_id: int,
    current_user: User = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """Schedule for today including co-workers."""
    target = _get_user_or_404(user_id, db)
    include_salary = _can_see_salary(current_user, target)
    all_users = db.query(User).filter(User.is_active == 1).all()
    today = get_today()
    absent_ids = {row[0] for row in db.query(Absence.user_id).filter(Absence.date == today).all()}
    return _build_day_status(
        target, today, db, include_salary=include_salary, all_users=all_users, absent_ids=absent_ids
    )


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
    """Schedule for a date range (?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD, max 31 days)."""
    target = _get_user_or_404(user_id, db)
    try:
        start = datetime.date.fromisoformat(from_date)
        end = datetime.date.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ogiltigt datumformat, använd YYYY-MM-DD") from None
    if end < start:
        raise HTTPException(status_code=400, detail="to_date måste vara efter from_date")
    if (end - start).days > 31:
        raise HTTPException(status_code=400, detail="Max 31 dagar per anrop")
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
    all_users = db.query(User).filter(User.is_active == 1).all()
    absent_ids = {row[0] for row in db.query(Absence.user_id).filter(Absence.date == target_date).all()}
    return _build_day_status(
        target, target_date, db, include_salary=include_salary, all_users=all_users, absent_ids=absent_ids
    )


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
        }
        for a in rows
    ]


# ── Next shift ───────────────────────────────────────────────────────────────


@router.get("/users/{user_id}/next-shift")
async def get_user_next_shift(
    user_id: int,
    current_user: User = Depends(get_api_user),
    db: Session = Depends(get_db),
):
    """Next working day for a user (starting from today). Returns shift details and date."""
    target = _get_user_or_404(user_id, db)
    today = get_today()

    for offset in range(60):
        candidate = today + datetime.timedelta(days=offset)
        absence = db.query(Absence).filter(Absence.user_id == target.id, Absence.date == candidate).first()
        if absence:
            continue
        shift, rotation_week = determine_shift_for_date(candidate, target.rotation_person_id)
        if shift and shift.code not in ("OFF",):
            return {
                "date": candidate.isoformat(),
                "days_from_today": offset,
                "shift": {
                    "code": shift.code,
                    "label": shift.label,
                    "start_time": shift.start_time,
                    "end_time": shift.end_time,
                    "color": shift.color,
                },
                "rotation_week": rotation_week,
            }

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
    """Middleware: kräver inloggad admin-session för docs-sidor."""
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


def _add_bearer_security(api: FastAPI) -> None:
    """Inject BearerAuth security scheme into the sub-app's OpenAPI schema."""
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
    _add_bearer_security(api)
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
    _add_bearer_security(api)
    return api
