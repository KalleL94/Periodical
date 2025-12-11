# app/routes/public.py
"""
Public routes for schedule views.
"""

from fastapi import (
    APIRouter,
    Request,
    Query,
    Depends,
    Form,
    HTTPException,
    status
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
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
    calculate_overtime_pay,
    get_overtime_shift_for_date,
    _cached_special_rules,
    _select_ob_rules_for_date,
    settings,
    weekday_names,
    person_wages,
    build_cowork_stats,
    build_cowork_details,
    persons as person_list,
)
from app.core.oncall import calculate_oncall_pay, calculate_oncall_pay_for_period, _cached_oncall_rules
from app.core.validators import validate_person_id, validate_date_params
from app.core.constants import PERSON_IDS
from app.core.utils import get_safe_today, get_navigation_dates
from app.core.helpers import (
    contrast_color,
    can_see_salary,
    strip_salary_data,
    render_template,
)
from app.auth.auth import get_current_user_optional, get_current_user
from app.database.database import User, UserRole, OvertimeShift, get_db

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
    original_shift = shift  # Keep track of original shift for OC calculation
    hours: float = 0.0
    start_dt: datetime | None = None
    end_dt: datetime | None = None

    if shift and shift.code != "OFF":
        hours, start_dt, end_dt = calculate_shift_hours(date_obj, shift)

    special_rules = _cached_special_rules(year)
    combined_rules = ob_rules + special_rules

    person = person_list[person_id - 1]
    monthly_salary = person_wages.get(person_id, settings.monthly_salary)

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

    if ot_shift:
        # Replace shift display with OT shift
        from app.core.storage import load_shift_types
        from app.core.models import ShiftType
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
                color=ot_shift_type.color
            )
            hours = ot_shift.hours

            # Parse OT shift times for calculations
            ot_start_full = ot_start_str if len(ot_start_str.split(":")) == 3 else ot_start_str + ":00"
            ot_end_full = ot_end_str if len(ot_end_str.split(":")) == 3 else ot_end_str + ":00"

            try:
                from datetime import datetime as dt
                start_time_obj = dt.strptime(ot_start_full, "%H:%M:%S").time()
                end_time_obj = dt.strptime(ot_end_full, "%H:%M:%S").time()
                start_dt = datetime.combine(date_obj, start_time_obj)
                end_dt = datetime.combine(date_obj, end_time_obj)
                if end_dt <= start_dt:
                    end_dt = end_dt + timedelta(days=1)
            except:
                pass

        ot_details = {
            "start_time": ot_shift.start_time,
            "end_time": ot_shift.end_time,
            "hours": ot_shift.hours,
            "pay": ot_shift.ot_pay,
            "hourly_rate": monthly_salary / 72
        }

    # Calculate on-call pay if this is an on-call shift (use original_shift to check)
    oncall_pay = 0.0
    oncall_details = {}

    if original_shift and original_shift.code == "OC":
        oncall_rules = _cached_oncall_rules(year)

        # Default: Full 24h calculation
        oc_calc = calculate_oncall_pay(date_obj, monthly_salary, oncall_rules)

        # If OT exists, we might need to recalculate if it interrupts the OC shift
        if ot_shift:
            # OC starts 06:00
            oc_start = datetime.combine(date_obj, time(6, 0))

            # Determine OT start time safely
            ot_start_time_val = ot_shift.start_time
            # Ensure we have a time object
            if isinstance(ot_start_time_val, str):
                try:
                    ot_start_time_val = datetime.strptime(ot_start_time_val, "%H:%M:%S").time()
                except ValueError:
                    ot_start_time_val = datetime.strptime(ot_start_time_val, "%H:%M").time()

            oc_end = datetime.combine(date_obj, ot_start_time_val)

            # If OT starts after OC starts, calculate partial
            if oc_end > oc_start:
                oc_calc = calculate_oncall_pay_for_period(
                    oc_start, 
                    oc_end, 
                    monthly_salary, 
                    oncall_rules
                )

        oncall_pay = oc_calc['total_pay']
        oncall_details = oc_calc

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
            "original_shift": original_shift,  # Pass original shift for OC detection
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
            "ot_shift": ot_details if show_salary and ot_details else None,
            "ot_shift_id": ot_shift_id,
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
    db: Session = Depends(get_db),
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

    days_in_month = summarize_month_for_person(year, month, person_id=person_id, session=db)

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
    db: Session = Depends(get_db),
):
    """Month view for all persons."""
    safe_today = get_safe_today(rotation_start_date)

    year = year or safe_today.year
    month = month or safe_today.month

    validate_date_params(year, month, None)

    persons = []
    for pid in range(1, 11):
        summary = summarize_month_for_person(year, month, pid, session=db)
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
    db: Session = Depends(get_db),
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

    year_data = summarize_year_for_person(year, person_id, session=db)
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
    db: Session = Depends(get_db),
):
    """Year view for all persons."""
    safe_today = get_safe_today(rotation_start_date)
    year = year or safe_today.year

    days_in_year = generate_year_data(year, session=db)

    person_ob_totals: list[float] = []
    for pid in PERSON_IDS:
        if can_see_salary(current_user, pid):
            total_pay = 0.0
            for m in range(1, 13):
                msum = summarize_month_for_person(year, m, pid, session=db)
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
    
# ============ Overtime Routes ============

@router.post("/overtime/add")
async def add_overtime_shift(
    user_id: int = Form(...),
    date: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    hours: float = Form(8.5),
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Add an overtime shift.

    Permissions:
    - Admin: can add for any user
    - User: can only add for themselves
    """
    # Permission check
    if current_user.role != UserRole.ADMIN and user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to add overtime for other users")

    # Get user's wage
    user = session.query(User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    monthly_salary = user.wage

    # Calculate OT pay
    ot_pay = calculate_overtime_pay(monthly_salary, hours)

    # Parse date
    ot_date = datetime.strptime(date, "%Y-%m-%d").date()

    # Parse times
    start_t = datetime.strptime(start_time, "%H:%M").time()
    end_t = datetime.strptime(end_time, "%H:%M").time()

    # Create overtime shift record
    ot_shift = OvertimeShift(
        user_id=user_id,
        date=ot_date,
        start_time=start_t,
        end_time=end_t,
        hours=hours,
        ot_pay=ot_pay,
        created_by=current_user.id
    )

    session.add(ot_shift)
    session.commit()

    return RedirectResponse(url=f"/day/{user_id}/{ot_date.year}/{ot_date.month}/{ot_date.day}", status_code=303)


@router.post("/overtime/{ot_id}/delete")
async def delete_overtime_shift(
    ot_id: int,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Delete an overtime shift.

    Permissions:
    - Admin: can delete any OT shift
    - User: can only delete their own OT shifts
    """
    ot_shift = session.query(OvertimeShift).get(ot_id)

    if not ot_shift:
        raise HTTPException(status_code=404, detail="Overtime shift not found")

    # Permission check
    if current_user.role != UserRole.ADMIN and ot_shift.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this overtime shift")

    # Save info for redirect
    user_id = ot_shift.user_id
    date = ot_shift.date

    # Delete
    session.delete(ot_shift)
    session.commit()

    return RedirectResponse(url=f"/day/{user_id}/{date.year}/{date.month}/{date.day}", status_code=303)
