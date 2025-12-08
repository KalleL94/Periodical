# main.py
"""
FastAPI application entry point with authentication.
"""

from fastapi import FastAPI, Request, Query, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from typing import Any
from datetime import date, datetime, timedelta, time

from app.routes.auth_routes import router as auth_router
from app.routes.admin import router as admin_router
from app.core.schedule import (
    determine_shift_for_date,
    build_week_data,
    rotation_start_date,
    summarize_month_for_person,
    summarize_year_for_person,
    generate_year_data,
    calculate_shift_hours,
    build_special_ob_rules_for_year,
    ob_rules,
    calculate_ob_hours,
    calculate_ob_pay,
    _cached_special_rules,
    _select_ob_rules_for_date,
    settings,
    weekday_names,
    person_wages,
    build_cowork_stats,
    build_cowork_details,
    persons as person_list,
)
from app.core.validators import validate_person_id, validate_date_params
from app.core.constants import PERSON_IDS
from app.core.utils import (
    get_safe_today,
    get_navigation_dates,
    render_template_response,
)

from app.core.types import PersonId, Year, Month, Day, Week, DayInfo

# Import auth dependencies
from app.auth.auth import get_current_user_optional, get_current_user
from app.database.database import create_tables, User, UserRole

# Create database tables on startup
create_tables()

app = FastAPI(title="ICA Schedule")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(auth_router)
app.include_router(admin_router)

templates = Jinja2Templates(directory="app/templates")

def _contrast_color(hex_color: str) -> str:
    """Return '#000' for light backgrounds, '#fff' for dark backgrounds."""
    if not hex_color:
        return "#fff"
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join([c*2 for c in h])
    try:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
    except Exception:
        return "#fff"
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#000" if lum > 0.5 else "#fff"


# Register a Jinja filter so templates can call `|contrast`
templates.env.filters["contrast"] = _contrast_color

def get_prev_next_week(year: int, week: int):
    monday = date.fromisocalendar(year, week, 1)

    prev_monday = monday - timedelta(weeks=1)
    next_monday = monday + timedelta(weeks=1)

    prev_year, prev_week, _ = prev_monday.isocalendar()
    next_year, next_week, _ = next_monday.isocalendar()

    return (prev_year, prev_week), (next_year, next_week)


def render_template_response(
    templates: Jinja2Templates,
    template_name: str,
    request: Request,
    context: dict,
    user: User | None = None,
):
    """Render template with user context."""
    ctx = {"request": request, "user": user}
    ctx.update(context)
    return templates.TemplateResponse(template_name, ctx)


def can_see_salary(current_user: User, target_person_id: int) -> bool:
    """Check if current user can see salary data for target person."""
    if current_user.role == UserRole.ADMIN:
        return True
    return current_user.id == target_person_id


# ============ Public routes (no login required for schedule view) ============


@app.get("/", response_class=HTMLResponse)
async def read_root(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    # Om inte inloggad, redirect till login
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    return render_template_response(
        templates,
        "index.html",
        request,
        {},
        user=current_user,
    )

@app.get("/day/{person_id}/{year}/{month}/{day}", response_class=HTMLResponse, name="day_person")
async def show_day_for_person(
    request: Request,
    person_id: int,
    year: int,
    month: int,
    day: int,
    current_user: User = Depends(get_current_user),
):
    # Validera person och datum
    person_id = validate_person_id(person_id)
    date = validate_date_params(year, month, day)

    nav = get_navigation_dates("day", date)
    iso_year, iso_week, _ = date.isocalendar()

    # Shift / hours
    shift, rotation_week = determine_shift_for_date(date, start_week=person_id)
    hours: float = 0.0
    start_dt: datetime | None = None
    end_dt: datetime | None = None

    if shift and shift.code != "OFF":
        hours, start_dt, end_dt = calculate_shift_hours(date, shift)

    # Special OB rules for this year (cached)
    special_rules = _cached_special_rules(year)
    combined_rules = ob_rules + special_rules

    # OB hours and pay
    person = person_list[person_id - 1]
    monthly_salary = person_wages.get(person_id, settings.monthly_salary)

    if start_dt and end_dt:
        ob_hours = calculate_ob_hours(start_dt, end_dt, combined_rules)
        ob_pay = calculate_ob_pay(
            start_dt,
            end_dt,
            combined_rules,
            monthly_salary
            )
    else:
        # No worked hours; keep base codes at zero
        ob_hours = {r.code: 0.0 for r in ob_rules}
        ob_pay = {r.code: 0.0 for r in ob_rules}

    ob_codes = sorted(ob_hours.keys())
    weekday_name = weekday_names[date.weekday()]

    # Active special OB rules for this calendar day
    midnight = datetime.combine(date, time(0, 0))
    active_special_rules = _select_ob_rules_for_date(midnight, special_rules)

    # Behörighetskontroll för lön/OB
    show_salary = can_see_salary(current_user, person_id)


    return render_template_response(
        templates,
        "day.html",
        request,
        {
            "person_id": person_id,
            "person_name": person.name,
            "date": target_date,
            "weekday_name": weekday_name,
            "rotation_week": rotation_week,
            "shift": shift,
            "hours": hours,
            "ob_hours": ob_hours if show_salary else {},
            "ob_pay": ob_pay if show_salary else {},
            "ob_codes": ob_codes if show_salary else [],
            "active_special_rules": active_special_rules,
            "iso_year": iso_year,
            "iso_week": iso_week,
            "show_salary": show_salary,
            **nav,
        },
        user=current_user,
    )

@app.get("/week/{person_id}", response_class=HTMLResponse, name="week_person")
async def show_week_for_person(
    request: Request,
    person_id: int,
    year: int = None,
    week: int = None,
    current_user: User = Depends(get_current_user),
):
    # Validera person
    person_id = validate_person_id(person_id)

    # Default-år/vecka baserat på "säkert" today
    safe_today = get_safe_today(rotation_start_date)
    iso_year, iso_week, _ = safe_today.isocalendar()
    
    year = year or iso_year
    week = week or iso_week
    
    days_in_week = build_week_data(year, week, person_id=person_id)

    # Beräkna prev/next för veckonavigering
    monday = date.fromisocalendar(year, week, 1)
    nav = get_navigation_dates("week", monday)

    # "today" i template ska vara riktiga dagens datum, precis som tidigare
    real_today = date.today()

    return render_template_response(
        templates,
        "week.html",
        request,
        {
            "year": year,
            "week": week,
            "days": days_in_week,
            "person_id": person_id,
            "today": real_today,
            **nav,
        },
        user=current_user,
    )

@app.get("/week", response_class=HTMLResponse, name="week_all")
async def show_week_all(
    request: Request,
    year: int = None,
    week: int = None,
    current_user: User = Depends(get_current_user),
):
    safe_today = get_safe_today(rotation_start_date)
    
        
    iso_year, iso_week, _ = safe_today.isocalendar()
    
    year = year or iso_year
    week = week or iso_week
    
    days_in_week = build_week_data(year, week)

    monday = date.fromisocalendar(year, week, 1)
    nav = get_navigation_dates("week", monday)
    
    real_today = date.today()

    return render_template_response(
        templates,
        "week_all.html",
        request,
        {
            "year": year,
            "week": week,
            "days": days_in_week,
            "today": real_today,
            **nav,
        },
        user=current_user,
    )

@app.get("/month/{person_id}", response_class=HTMLResponse, name="month_person")
async def show_month_for_person(
    request: Request,
    person_id: int,
    year: int = None,
    month: int = None,
):
    # Validera person
    person_id = validate_person_id(person_id)

    safe_today = get_safe_today(rotation_start_date)
    

        
    year = year or safe_today.year
    month = month or safe_today.month

    # Validera år/månad
    validate_date_params(year, month, None)
    
    days_in_month = summarize_month_for_person(year, month, person_id=person_id)
        
    return render_template_response(
        templates,
        "month.html",
        request,
        {
            "year": year,
            "month": month,
            "person_id": person_id,
            "person_name": person_list[person_id - 1].name,
            "days": days_in_month,
        },
    )


@app.get("/month", response_class=HTMLResponse, name="month_all")
async def show_month_all(
    request: Request,
    year: int = None,
    month: int = None,
):
    safe_today = get_safe_today(rotation_start_date)

    

    year = year or safe_today.year
    month = month or safe_today.month

    # Validera år/månad
    validate_date_params(year, month, None)

    # Build summary for all 10 persons
    persons = []
    for pid in range(1, 11):
        summary = summarize_month_for_person(year, month, pid)
        persons.append(summary)

    return render_template_response(
        templates,
        "month_all.html",
        request,
        {
            "year": year,
            "month": month,
            "persons": persons,
        },
    )


@app.get("/year/{person_id}", response_class=HTMLResponse, name="year_person")
async def year_view(
    request: Request,
    person_id: int,
    year: int = Query(None),
    with_person_id: int | None = Query(None, alias="with_person_id"),
):
    # Validera personparametrar
    person_id = validate_person_id(person_id)
    if with_person_id is not None:
        with_person_id = validate_person_id(with_person_id)
    
    safe_today = get_safe_today(rotation_start_date)
    year = year or safe_today.year

    months: list[dict] = []
    for m in range(1,13):
        months.append(summarize_month_for_person(year, m, person_id))

    person = person_list[person_id - 1]

    # Cowork data
    cowork_rows = build_cowork_stats(year, person_id)
    selected_other_id = None
    selected_other_name = None
    cowork_details: list[dict] = []

    if with_person_id:
        selected_other_id = with_person_id
        selected_other_name = person_list[with_person_id - 1].name
        cowork_details = build_cowork_details(year, person_id, with_person_id)

    # Year summary and per month data
    year_data = summarize_year_for_person(year, person_id)
    months = year_data["months"]
    year_summary = year_data["year_summary"]

    return render_template_response(
        templates,
        "year.html",
        request,
        {
            "year": year,
            "person_id": person_id,
            "person_name": person.name,
            "months": months,
            "year_summary": year_summary,
            "cowork_rows": cowork_rows,
            "cowork_details": cowork_details,
            "selected_other_id": selected_other_id,
            "selected_other_name": selected_other_name,
        },
    )


@app.get("/year", response_class=HTMLResponse, name="year_all")
async def show_year_all(
    request: Request,
    year: int = None,
):
    safe_today = get_safe_today(rotation_start_date)
    year = year or safe_today.year

    # Build full-year day list with per-person shifts using generate_year_data
    days_in_year = generate_year_data(year)

    # Compute total OB pay per person for the year by summing monthly summaries
    person_ob_totals: list[float] = []
    for pid in PERSON_IDS:
        total_pay = 0.0
        for m in range(1, 13):
            msum = summarize_month_for_person(year, m, pid)
            total_pay += sum(msum.get("ob_pay", {}).values())
        person_ob_totals.append(total_pay)

    return render_template_response(
        templates,
        "year_all.html",
        request,
        {
            "year": year,
            "days": days_in_year,
            "person_ob_totals": person_ob_totals,
        },
    )