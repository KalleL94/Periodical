# app/routes/public.py
"""
Public routes for schedule views.
"""

from fastapi import APIRouter, Request, Query, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from datetime import date, datetime, time

from app.core.schedule import (
    determine_shift_for_date,
    build_week_data,
    rotation_start_date,
    summarize_month_for_person,
    summarize_year_for_person,
    generate_year_data,
    calculate_shift_hours,
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
from app.core.oncall import calculate_oncall_pay, _cached_oncall_rules
from app.core.validators import validate_person_id, validate_date_params
from app.core.constants import PERSON_IDS
from app.core.utils import get_safe_today, get_navigation_dates
from app.core.helpers import (
    contrast_color,
    can_see_salary,
    strip_salary_data,
    render_template,
)
from app.auth.auth import get_current_user_optional
from app.database.database import User, UserRole

router = APIRouter(tags=["public"])
templates = Jinja2Templates(directory="app/templates")

# Register Jinja filter for contrast color on badges
templates.env.filters["contrast"] = contrast_color


# ============ Routes ============

@router.get("/", response_class=HTMLResponse)
async def read_root(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Home page - redirect to login if not authenticated."""
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    return render_template(
        templates,
        "index.html",
        request,
        {},
        user=current_user,
    )


@router.get("/day/{person_id}/{year}/{month}/{day}", response_class=HTMLResponse, name="day_person")
async def show_day_for_person(
    request: Request,
    person_id: int,
    year: int,
    month: int,
    day: int,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Day view for a specific person."""
    if current_user is None:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)

    person_id = validate_person_id(person_id)

    if current_user.role != UserRole.ADMIN and current_user.id != person_id:
        return RedirectResponse(
            url=f"/day/{current_user.id}/{year}/{month}/{day}",
            status_code=302,
        )

    date_obj = validate_date_params(year, month, day)
    nav = get_navigation_dates("day", date_obj)
    iso_year, iso_week, _ = date_obj.isocalendar()

    shift, rotation_week = determine_shift_for_date(date_obj, start_week=person_id)
    hours: float = 0.0
    start_dt: datetime | None = None
    end_dt: datetime | None = None

    if shift and shift.code != "OFF":
        hours, start_dt, end_dt = calculate_shift_hours(date_obj, shift)

    special_rules = _cached_special_rules(year)
    combined_rules = ob_rules + special_rules

    person = person_list[person_id - 1]
    monthly_salary = person_wages.get(person_id, settings.monthly_salary)

    if start_dt and end_dt:
        ob_hours = calculate_ob_hours(start_dt, end_dt, combined_rules)
        ob_pay = calculate_ob_pay(start_dt, end_dt, combined_rules, monthly_salary)
    else:
        ob_hours = {r.code: 0.0 for r in ob_rules}
        ob_pay = {r.code: 0.0 for r in ob_rules}

    ob_codes = sorted(ob_hours.keys())
    weekday_name = weekday_names[date_obj.weekday()]

    midnight = datetime.combine(date_obj, time(0, 0))
    active_special_rules = _select_ob_rules_for_date(midnight, special_rules)

    # Calculate on-call pay if this is an on-call shift
    oncall_pay = 0.0
    oncall_details = {}
    if shift and shift.code == "OC":
        oncall_rules = _cached_oncall_rules(year)
        oncall_calc = calculate_oncall_pay(date_obj, monthly_salary, oncall_rules)
        oncall_pay = oncall_calc['total_pay']
        oncall_details = oncall_calc

    show_salary = can_see_salary(current_user, person_id)

    return render_template(
        templates,
        "day.html",
        request,
        {
            "person_id": person_id,
            "person_name": person.name,
            "date": date_obj,
            "weekday_name": weekday_name,
            "rotation_week": rotation_week,
            "shift": shift,
            "hours": hours,
            "ob_hours": ob_hours if show_salary else {},
            "ob_pay": ob_pay if show_salary else {},
            "ob_codes": ob_codes if show_salary else [],
            "active_special_rules": active_special_rules,
            "oncall_pay": oncall_pay if show_salary else 0.0,
            "oncall_details": oncall_details if show_salary else {},
            "monthly_salary": monthly_salary,
            "iso_year": iso_year,
            "iso_week": iso_week,
            "show_salary": show_salary,
            **nav,
        },
        user=current_user,
    )


@router.get("/week/{person_id}", response_class=HTMLResponse, name="week_person")
async def show_week_for_person(
    request: Request,
    person_id: int,
    year: int = None,
    week: int = None,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Week view for a specific person."""
    if current_user is None:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)

    person_id = validate_person_id(person_id)

    safe_today = get_safe_today(rotation_start_date)
    iso_year, iso_week, _ = safe_today.isocalendar()

    year = year or iso_year
    week = week or iso_week

    if current_user.role != UserRole.ADMIN and current_user.id != person_id:
        return RedirectResponse(
            url=f"/week/{current_user.id}?year={year}&week={week}",
            status_code=302,
        )

    days_in_week = build_week_data(year, week, person_id=person_id)

    monday = date.fromisocalendar(year, week, 1)
    nav = get_navigation_dates("week", monday)

    real_today = date.today()

    return render_template(
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


@router.get("/week", response_class=HTMLResponse, name="week_all")
async def show_week_all(
    request: Request,
    year: int = None,
    week: int = None,
    current_user: User = Depends(get_current_user_optional),
):
    """Week view for all persons."""
    safe_today = get_safe_today(rotation_start_date)

    iso_year, iso_week, _ = safe_today.isocalendar()

    year = year or iso_year
    week = week or iso_week

    days_in_week = build_week_data(year, week)

    monday = date.fromisocalendar(year, week, 1)
    nav = get_navigation_dates("week", monday)

    real_today = date.today()

    return render_template(
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


@router.get("/month/{person_id}", response_class=HTMLResponse, name="month_person")
async def show_month_for_person(
    request: Request,
    person_id: int,
    year: int = None,
    month: int = None,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Month view for a specific person."""
    if current_user is None:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)

    person_id = validate_person_id(person_id)

    safe_today = get_safe_today(rotation_start_date)

    year = year or safe_today.year
    month = month or safe_today.month

    if current_user.role != UserRole.ADMIN and current_user.id != person_id:
        return RedirectResponse(
            url=f"/month/{current_user.id}?year={year}&month={month}",
            status_code=302,
        )

    validate_date_params(year, month, None)

    days_in_month = summarize_month_for_person(year, month, person_id=person_id)

    show_salary = can_see_salary(current_user, person_id)

    if not show_salary:
        days_in_month = strip_salary_data(days_in_month)

    return render_template(
        templates,
        "month.html",
        request,
        {
            "year": year,
            "month": month,
            "person_id": person_id,
            "person_name": person_list[person_id - 1].name,
            "days": days_in_month,
            "show_salary": show_salary,
        },
        user=current_user,
    )


@router.get("/month", response_class=HTMLResponse, name="month_all")
async def show_month_all(
    request: Request,
    year: int = None,
    month: int = None,
    current_user: User = Depends(get_current_user_optional),
):
    """Month view for all persons."""
    safe_today = get_safe_today(rotation_start_date)

    year = year or safe_today.year
    month = month or safe_today.month

    validate_date_params(year, month, None)

    persons = []
    for pid in range(1, 11):
        summary = summarize_month_for_person(year, month, pid)
        if not can_see_salary(current_user, pid):
            summary = strip_salary_data(summary)
        persons.append(summary)

    show_salary = current_user is not None and current_user.role == UserRole.ADMIN

    return render_template(
        templates,
        "month_all.html",
        request,
        {
            "year": year,
            "month": month,
            "persons": persons,
            "show_salary": show_salary,
        },
        user=current_user,
    )


@router.get("/year/{person_id}", response_class=HTMLResponse, name="year_person")
async def year_view(
    request: Request,
    person_id: int,
    year: int = Query(None),
    with_person_id: int | None = Query(None, alias="with_person_id"),
    current_user: User | None = Depends(get_current_user_optional),
):
    """Year view for a specific person."""
    if current_user is None:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)

    person_id = validate_person_id(person_id)

    if current_user.role != UserRole.ADMIN and current_user.id != person_id:
        return RedirectResponse(
            url=f"/year/{current_user.id}?year={year or ''}",
            status_code=302,
        )

    if with_person_id is not None:
        with_person_id = validate_person_id(with_person_id)

    safe_today = get_safe_today(rotation_start_date)
    year = year or safe_today.year

    person = person_list[person_id - 1]

    cowork_rows = build_cowork_stats(year, person_id)
    selected_other_id = None
    selected_other_name = None
    cowork_details: list[dict] = []

    if with_person_id:
        selected_other_id = with_person_id
        selected_other_name = person_list[with_person_id - 1].name
        cowork_details = build_cowork_details(year, person_id, with_person_id)

    year_data = summarize_year_for_person(year, person_id)
    months = year_data["months"]
    year_summary = year_data["year_summary"]

    show_salary = can_see_salary(current_user, person_id)

    if not show_salary:
        months = [strip_salary_data(m) for m in months]
        year_summary = strip_salary_data(year_summary)

    return render_template(
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
            "show_salary": show_salary,
        },
        user=current_user,
    )


@router.get("/year", response_class=HTMLResponse, name="year_all")
async def show_year_all(
    request: Request,
    year: int = None,
    current_user: User = Depends(get_current_user_optional),
):
    """Year view for all persons."""
    safe_today = get_safe_today(rotation_start_date)
    year = year or safe_today.year

    days_in_year = generate_year_data(year)

    person_ob_totals: list[float] = []
    for pid in PERSON_IDS:
        if can_see_salary(current_user, pid):
            total_pay = 0.0
            for m in range(1, 13):
                msum = summarize_month_for_person(year, m, pid)
                total_pay += sum(msum.get("ob_pay", {}).values())
            person_ob_totals.append(total_pay)
        else:
            person_ob_totals.append(None)

    show_salary = current_user is not None and current_user.role == UserRole.ADMIN

    return render_template(
        templates,
        "year_all.html",
        request,
        {
            "year": year,
            "days": days_in_year,
            "person_ob_totals": person_ob_totals,
            "show_salary": show_salary,
        },
        user=current_user,
    )