# app/routes/schedule_personal.py
"""
Personal schedule view routes - day, week, month, and year views for specific persons.
"""

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user_optional
from app.core.helpers import can_see_salary, render_template, strip_salary_data
from app.core.logging_config import get_logger
from app.core.oncall import (
    _cached_oncall_rules,
    _get_storhelg_dates_for_year,
    calculate_oncall_pay,
    calculate_oncall_pay_for_period,
)
from app.core.schedule import (
    _cached_special_rules,
    _select_ob_rules_for_date,
    build_calendar_grid_for_month,
    build_cowork_details,
    build_cowork_stats,
    build_week_data,
    calculate_ob_hours,
    calculate_ob_pay,
    calculate_shift_hours,
    determine_shift_for_date,
    get_overtime_shift_for_date,
    get_rotation_length_for_date,
    get_user_wage,
    ob_rules,
    rotation_start_date,
    settings,
    summarize_year_for_person,
    weekday_names,
)
from app.core.schedule import (
    persons as person_list,
)
from app.core.utils import get_navigation_dates, get_ot_shift_display_code, get_safe_today, get_today
from app.core.validators import validate_date_params, validate_person_id
from app.database.database import Absence, User, UserRole, get_db
from app.routes.shared import templates

logger = get_logger(__name__)

router = APIRouter(tags=["schedule_personal"])


@router.get("/day/{person_id}/{year}/{month}/{day}", response_class=HTMLResponse, name="day_person")
async def show_day_for_person(
    request: Request,
    person_id: int,
    year: int,
    month: int,
    day: int,
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
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
    rotation_length = get_rotation_length_for_date(date_obj)
    original_shift = shift  # Keep track of original shift for OC calculation
    hours: float = 0.0
    start_dt: datetime | None = None
    end_dt: datetime | None = None

    if shift and shift.code != "OFF":
        hours, start_dt, end_dt = calculate_shift_hours(date_obj, shift)

    special_rules = _cached_special_rules(year)
    combined_rules = ob_rules + special_rules

    person = person_list[person_id - 1]
    # Use temporal wage query for the specific date being viewed
    monthly_salary = get_user_wage(db, person_id, settings.monthly_salary, effective_date=date_obj)

    # OT shifts never have OB pay, so check if this will become an OT shift
    # We need to check this before fetching the OT shift
    temp_ot_check = get_overtime_shift_for_date(db, person_id, date_obj)

    if start_dt and end_dt and not temp_ot_check:
        # Only calculate OB if there's NO overtime shift
        ob_hours = calculate_ob_hours(start_dt, end_dt, combined_rules)
        ob_pay = calculate_ob_pay(start_dt, end_dt, combined_rules, monthly_salary)
    else:
        # No OB for OT shifts
        ob_hours = {r.code: 0.0 for r in ob_rules}
        ob_pay = {r.code: 0.0 for r in ob_rules}

    ob_codes = sorted(ob_hours.keys())
    weekday_name = weekday_names[date_obj.weekday()]

    midnight = datetime.combine(date_obj, time(0, 0))
    active_special_rules = _select_ob_rules_for_date(midnight, special_rules)

    # Fetch Overtime Shift from DB
    ot_shift = get_overtime_shift_for_date(db, person_id, date_obj)
    ot_shift_id = ot_shift.id if ot_shift else None
    ot_details = {}

    # Fetch absence for this person and date (check before calculating OT)
    absence = db.query(Absence).filter(Absence.user_id == person_id, Absence.date == date_obj).first()

    if ot_shift and not absence:  # Skip OT if there's an absence
        # Replace shift display with OT shift
        from app.core.models import ShiftType
        from app.core.storage import load_shift_types

        all_shifts = load_shift_types()
        ot_shift_type = next((s for s in all_shifts if s.code == "OT"), None)
        if ot_shift_type:
            # Create a copy of the OT shift type with actual times from database
            ot_start_str = str(ot_shift.start_time)
            ot_end_str = str(ot_shift.end_time)

            # Remove seconds if present (format as HH:MM)
            if len(ot_start_str.split(":")) == 3:
                ot_start_str = ":".join(ot_start_str.split(":")[:2])
            if len(ot_end_str.split(":")) == 3:
                ot_end_str = ":".join(ot_end_str.split(":")[:2])

            # Create custom shift with actual OT times
            shift = ShiftType(
                code="OT",
                label=ot_shift_type.label,
                start_time=ot_start_str,
                end_time=ot_end_str,
                color=ot_shift_type.color,
            )
            hours = ot_shift.hours

            # Parse OT shift times for calculations
            ot_start_full = ot_start_str if len(ot_start_str.split(":")) == 3 else ot_start_str + ":00"
            ot_end_full = ot_end_str if len(ot_end_str.split(":")) == 3 else ot_end_str + ":00"

            try:
                start_time_obj = datetime.strptime(ot_start_full, "%H:%M:%S").time()
                end_time_obj = datetime.strptime(ot_end_full, "%H:%M:%S").time()
                start_dt = datetime.combine(date_obj, start_time_obj)
                end_dt = datetime.combine(date_obj, end_time_obj)
                if end_dt <= start_dt:
                    end_dt = end_dt + timedelta(days=1)
            except ValueError:
                pass

        # Recalculate overtime pay based on historical wage
        from app.core.constants import OT_RATE_DIVISOR

        hourly_rate = monthly_salary / OT_RATE_DIVISOR
        ot_pay = hourly_rate * ot_shift.hours

        ot_details = {
            "start_time": ot_shift.start_time,
            "end_time": ot_shift.end_time,
            "hours": ot_shift.hours,
            "pay": ot_pay,
            "hourly_rate": hourly_rate,
        }

    # Calculate on-call pay if this is an on-call shift (use original_shift to check)
    oncall_pay = 0.0
    oncall_details = {}

    if original_shift and original_shift.code == "OC":
        oncall_rules = _cached_oncall_rules(year)

        # Default: Full 24h calculation
        oc_calc = calculate_oncall_pay(date_obj, monthly_salary, oncall_rules)

        # If OT exists, recalculate OC pay only for period BEFORE OT starts
        if ot_shift:
            # OC shift runs 00:00 to 00:00 next day
            oc_start = datetime.combine(date_obj, time(0, 0))

            # Parse OT start time
            ot_start_time_val = ot_shift.start_time

            # Ensure we have time object
            if isinstance(ot_start_time_val, str):
                try:
                    ot_start_time_val = datetime.strptime(ot_start_time_val, "%H:%M:%S").time()
                except ValueError:
                    ot_start_time_val = datetime.strptime(ot_start_time_val, "%H:%M").time()

            oc_end = datetime.combine(date_obj, ot_start_time_val)

            # Calculate OC pay only for period before OT starts (00:00 to OT start)
            if oc_end > oc_start:
                oc_calc = calculate_oncall_pay_for_period(oc_start, oc_end, monthly_salary, oncall_rules)
                oncall_pay = oc_calc["total_pay"]
            else:
                # OT starts at or before OC start, no OC pay
                oncall_pay = 0.0
                oc_calc = {"total_pay": 0.0, "breakdown": {}, "total_hours": 0.0}
        else:
            oncall_pay = oc_calc["total_pay"]

        oncall_details = oc_calc

    show_salary = can_see_salary(current_user, person_id)

    # Check if this date is a storhelg (major holiday)
    storhelg_dates = _get_storhelg_dates_for_year(year)
    is_storhelg = date_obj in storhelg_dates

    # Calculate absence deduction if absence exists
    absence_deduction = 0.0
    absence_shift_hours = 0.0
    is_karens = False

    if absence and show_salary:
        from app.core.schedule.wages import calculate_absence_deduction, get_shift_hours_for_date

        # Get shift hours for the day
        absence_shift_hours = get_shift_hours_for_date(db, person_id, date_obj)

        # Check if this is a karensdag (first sick day in a period)
        if absence.absence_type.value == "SICK":
            # Check if there was a sick day within the last 5 days
            five_days_ago = date_obj - timedelta(days=5)
            previous_sick = (
                db.query(Absence)
                .filter(
                    Absence.user_id == person_id,
                    Absence.date >= five_days_ago,
                    Absence.date < date_obj,
                    Absence.absence_type == absence.absence_type,
                )
                .first()
            )
            is_karens = previous_sick is None

        # Calculate deduction
        absence_deduction = calculate_absence_deduction(
            monthly_salary, absence.absence_type.value, absence_shift_hours, is_karens
        )

    # Get coworkers for this day
    from app.core.schedule import generate_period_data
    from app.core.schedule.cowork import get_coworkers_for_day

    # Fetch all persons' data for this single day
    all_persons_day = generate_period_data(date_obj, date_obj, person_id=None, session=db)

    persons_today = []
    persons_today_with_shift = []
    if all_persons_day and len(all_persons_day) > 0:
        persons_today = all_persons_day[0].get("persons", [])
    for p in persons_today:
        p_shift = p.get("shift")
        if p_shift and p_shift.code != "OFF":
            if p_shift.code == "OT":
                # Use helper function to get the display code for OT shifts
                p_shift_code = get_ot_shift_display_code(p.get("start"))
                persons_today_with_shift.append((p.get("person_name"), p_shift_code))
            else:
                persons_today_with_shift.append((p.get("person_name"), p_shift.code))
    # Sort by 2nd item (shift code), then by name
    persons_today_with_shift.sort(key=lambda x: (x[1], x[0]))

    # Determine shift code for coworker matching
    actual_shift_obj = shift
    if actual_shift_obj and actual_shift_obj.code == "OT":
        # If target has OT, use original_shift if it's a work shift, else use "OT"
        if original_shift and original_shift.code in ("N1", "N2", "N3"):
            shift_code_for_matching = original_shift.code
        else:
            shift_code_for_matching = "OT"
    else:
        # Use original_shift if available, otherwise actual shift
        shift_for_matching = original_shift if original_shift else actual_shift_obj
        shift_code_for_matching = shift_for_matching.code if shift_for_matching else "OFF"

    coworkers = get_coworkers_for_day(person_id, shift_code_for_matching, persons_today, start_dt, end_dt)

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
            "rotation_length": rotation_length,
            "shift": shift,
            "original_shift": original_shift,  # Pass original shift for OC detection
            "hours": hours,
            "ob_hours": ob_hours if show_salary else {},
            "ob_pay": ob_pay if show_salary else {},
            "ob_codes": ob_codes if show_salary else [],
            "ob_rules": combined_rules,  # All OB rules for label lookup
            "active_special_rules": active_special_rules,
            "oncall_pay": oncall_pay if show_salary else 0.0,
            "oncall_details": oncall_details if show_salary else {},
            "monthly_salary": monthly_salary,
            "iso_year": iso_year,
            "iso_week": iso_week,
            "show_salary": show_salary,
            "is_storhelg": is_storhelg,  # Whether this date is a major holiday
            "ot_shift": ot_details if show_salary and ot_details else None,
            "ot_shift_id": ot_shift_id,
            "absence": absence,  # Pass absence data to template
            "absence_deduction": absence_deduction,  # Deduction amount in SEK
            "absence_shift_hours": absence_shift_hours,  # Hours for the shift
            "is_karens": is_karens,  # Whether this is a karensdag
            "coworkers": coworkers,  # List of coworker names
            "all_working_persons": persons_today_with_shift,
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
    db: Session = Depends(get_db),
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

    days_in_week = build_week_data(year, week, person_id=person_id, session=db, include_coworkers=True)

    monday = date.fromisocalendar(year, week, 1)
    nav = get_navigation_dates("week", monday)

    real_today = get_today()

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


@router.get("/month/{person_id}", response_class=HTMLResponse, name="month_person")
async def show_month_for_person(
    request: Request,
    person_id: int,
    year: int = None,
    month: int = None,
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Month view for a specific person."""
    start_time = datetime.now()

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

    calendar_data = build_calendar_grid_for_month(year, month, person_id=person_id, session=db, include_coworkers=True)
    days_in_month = calendar_data["summary"]
    calendar_grid = calendar_data["grid"]

    show_salary = can_see_salary(current_user, person_id)

    if not show_salary:
        days_in_month = strip_salary_data(days_in_month)

    # Calculate and log load time
    end_time = datetime.now()
    load_time = (end_time - start_time).total_seconds()
    logger.info(
        f"Route /month/{person_id} (year={year}, month={month}) loaded in {load_time:.3f}s",
        extra={
            "duration_ms": load_time * 1000,
            "path": f"/month/{person_id}",
            "user_id": current_user.id if current_user else None,
        },
    )

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
            "calendar_grid": calendar_grid,
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
    db: Session = Depends(get_db),
):
    """Year view for a specific person."""
    start_time = datetime.now()

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

    year_data = summarize_year_for_person(year, person_id, session=db)
    months = year_data["months"]
    year_summary = year_data["year_summary"]

    # Get OB rules for label lookup
    special_rules = _cached_special_rules(year)
    combined_rules = ob_rules + special_rules

    show_salary = can_see_salary(current_user, person_id)

    if not show_salary:
        months = [strip_salary_data(m) for m in months]
        year_summary = strip_salary_data(year_summary)

    # Calculate and log load time
    end_time = datetime.now()
    load_time = (end_time - start_time).total_seconds()

    logger.info(
        f"Route /year/{person_id} loaded in {load_time:.3f}s",
        extra={
            "duration_ms": load_time * 1000,
            "path": f"/year/{person_id}",
            "user_id": current_user.id if current_user else None,
        },
    )

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
            "ob_rules": combined_rules,  # All OB rules for label lookup
        },
        user=current_user,
    )
