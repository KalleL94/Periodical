# app/routes/schedule_all.py
"""
Team-wide schedule view routes - week, month, and year views for all persons.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user_optional
from app.core.helpers import can_see_salary, render_template, strip_salary_data
from app.core.logging_config import get_logger
from app.core.schedule import (
    build_week_data,
    generate_month_data,
    generate_year_data,
    get_all_user_wages,
    rotation_start_date,
    summarize_month_for_person,
)
from app.core.utils import get_navigation_dates, get_safe_today, get_today
from app.core.validators import validate_date_params
from app.database.database import User, UserRole, get_db
from app.routes.shared import templates

logger = get_logger(__name__)

router = APIRouter(tags=["schedule_all"])


@router.get("/week", response_class=HTMLResponse, name="week_all")
async def show_week_all(
    request: Request,
    year: int = None,
    week: int = None,
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Week view for all persons."""
    safe_today = get_safe_today(rotation_start_date)

    iso_year, iso_week, _ = safe_today.isocalendar()

    year = year or iso_year
    week = week or iso_week

    days_in_week = build_week_data(year, week, session=db)

    monday = date.fromisocalendar(year, week, 1)
    nav = get_navigation_dates("week", monday)

    real_today = get_today()

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


@router.get("/month", response_class=HTMLResponse, name="month_all")
async def show_month_all(
    request: Request,
    year: int = None,
    month: int = None,
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Month view for all persons."""
    start_time = datetime.now()

    safe_today = get_safe_today(rotation_start_date)

    year = year or safe_today.year
    month = month or safe_today.month

    validate_date_params(year, month, None)

    # Pre-load wages once to avoid N+1 queries (10 persons × 1 query each)
    user_wages = get_all_user_wages(db)

    # Only fetch tax tables if user is admin (needed for salary calculations)
    is_admin = current_user is not None and current_user.role == UserRole.ADMIN

    persons = []
    for pid in range(1, 11):
        # Generate MONTH data ONCE per person (30-31 days instead of 365 days - 12x faster!)
        person_month_days = generate_month_data(year, month, pid, session=db, user_wages=user_wages)

        summary = summarize_month_for_person(
            year,
            month,
            pid,
            session=db,
            user_wages=user_wages,
            year_days=person_month_days,
            fetch_tax_table=is_admin,
            payment_year=year,
        )
        if not can_see_salary(current_user, pid):
            summary = strip_salary_data(summary)
        persons.append(summary)

    show_salary = current_user is not None and current_user.role == UserRole.ADMIN

    # Calculate and log load time
    end_time = datetime.now()
    load_time = (end_time - start_time).total_seconds()
    logger.info(
        f"Route /month (all persons) (year={year}, month={month}) loaded in {load_time:.3f}s",
        extra={"duration_ms": load_time * 1000, "path": "/month", "user_id": current_user.id if current_user else None},
    )

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


@router.get("/year", response_class=HTMLResponse, name="year_all")
async def show_year_all(
    request: Request,
    year: int = None,
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Year view for all persons."""
    start_time = datetime.now()

    safe_today = get_safe_today(rotation_start_date)
    year = year or safe_today.year

    # Pre-load wages once to avoid N+1 queries (10 persons × 12 months = 120 queries → 1 query)
    user_wages = get_all_user_wages(db)

    days_in_year = generate_year_data(year, session=db, user_wages=user_wages)

    # Skip calculating totals on initial load - will be lazy-loaded via AJAX
    # This makes initial page load much faster (~0.5s instead of 1-3s)
    person_ob_totals = None

    show_salary = current_user is not None and current_user.role == UserRole.ADMIN

    # Calculate and log load time
    end_time = datetime.now()
    load_time = (end_time - start_time).total_seconds()

    logger.info(
        f"Route /year (all persons) loaded in {load_time:.3f}s",
        extra={"duration_ms": load_time * 1000, "path": "/year", "user_id": current_user.id if current_user else None},
    )

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
