# app/routes/schedule_personal.py
"""
Personal schedule view routes - day, week, month, and year views for specific persons.
"""

import io
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user_optional
from app.core.helpers import can_see_salary, render_template, strip_salary_data
from app.core.holidays import get_holiday_dates_for_year
from app.core.logging_config import get_logger
from app.core.oncall import (
    _cached_oncall_rules as _get_oncall_rules,
)
from app.core.oncall import (
    _get_storhelg_dates_for_year,
    apply_oncall_hours_override,
    compute_oncall_details,
)
from app.core.schedule import (
    _cached_special_rules,
    _select_ob_rules_for_date,
    build_calendar_grid_for_month,
    build_cowork_details,
    build_cowork_stats,
    build_handover_details,
    build_week_data,
    calculate_ob_hours,
    calculate_ob_pay,
    calculate_shift_hours,
    compute_ot_details,
    determine_shift_for_date,
    get_effective_monthly_wage,
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
from app.core.schedule.ob import apply_ob_hours_override
from app.core.schedule.vacation import calculate_vacation_balance
from app.core.utils import get_navigation_dates, get_ot_shift_display_code, get_safe_today, get_today
from app.core.validators import validate_date_params, validate_person_id
from app.database.database import (
    Absence,
    AbsenceType,
    DayPayOverride,
    OnCallOverride,
    OnCallOverrideType,
    ShiftOverride,
    User,
    UserRole,
    get_db,
)
from app.routes.shared import redirect_if_not_own_data, templates

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
    """Day view for a specific person.

    The person_id parameter can be:
    - 1-10: A rotation position (legacy, still supported)
    - > 10: A user_id (e.g., 11 for Rickard who has rotation position 3)
    """
    if current_user is None:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)

    # Handle both user_id (>10) and rotation position (1-10)
    if person_id > 10:
        target_user = db.query(User).filter(User.id == person_id).first()
        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")

        user_id_for_wages = person_id
        rotation_position = target_user.rotation_person_id
    else:
        person_id = validate_person_id(person_id)
        user_id_for_wages = person_id
        rotation_position = person_id

    if redirect := redirect_if_not_own_data(
        current_user, user_id_for_wages, f"/day/{current_user.id}/{year}/{month}/{day}"
    ):
        return redirect

    date_obj = validate_date_params(year, month, day)
    nav = get_navigation_dates("day", date_obj)
    iso_year, iso_week, _ = date_obj.isocalendar()

    # Use rotation_position for schedule calculation
    shift, rotation_week = determine_shift_for_date(date_obj, start_week=rotation_position)
    rotation_length = get_rotation_length_for_date(date_obj)
    original_shift = shift  # Keep track of original shift for OC calculation

    # Check if date is before user's employment started - show OFF if so
    from app.core.schedule.person_history import get_current_person_for_position, get_employment_period

    before_employment = False
    if person_id > 10:
        emp_start, _ = get_employment_period(db, target_user.id, rotation_position)
        if emp_start and date_obj < emp_start:
            before_employment = True
    else:
        current_person = get_current_person_for_position(db, rotation_position)
        if current_person and current_person.get("effective_from"):
            if date_obj < current_person["effective_from"]:
                before_employment = True

    if before_employment:
        from app.core.storage import load_shift_types

        all_shifts = load_shift_types()
        off_shift = next((s for s in all_shifts if s.code == "OFF"), None)
        if off_shift:
            shift = off_shift

    # Fetch oncall override EARLY to apply before hours calculation
    # Use user_id_for_wages since oncall overrides are stored per user
    oncall_override = (
        db.query(OnCallOverride)
        .filter(OnCallOverride.user_id == user_id_for_wages, OnCallOverride.date == date_obj)
        .first()
    )

    # Determine if this person has OC in the rotation (before any overrides)
    has_rotation_oc = original_shift and original_shift.code == "OC"

    # Apply oncall override to shift
    if oncall_override:
        from app.core.storage import load_shift_types

        all_shifts = load_shift_types()
        if oncall_override.override_type == OnCallOverrideType.ADD:
            # ADD override: change shift to OC
            oc_shift = next((s for s in all_shifts if s.code == "OC"), None)
            if oc_shift:
                shift = oc_shift
        elif oncall_override.override_type == OnCallOverrideType.REMOVE:
            # REMOVE override: if shift is OC, change to OFF
            if shift and shift.code == "OC":
                off_shift = next((s for s in all_shifts if s.code == "OFF"), None)
                if off_shift:
                    shift = off_shift

    # Determine if this is effectively an OC shift (considering overrides)
    is_effective_oc = (
        has_rotation_oc and not (oncall_override and oncall_override.override_type == OnCallOverrideType.REMOVE)
    ) or (oncall_override and oncall_override.override_type == OnCallOverrideType.ADD)

    # Fetch and apply manual shift override (N1/N2/N3 assigned by admin)
    shift_override = (
        db.query(ShiftOverride)
        .filter(ShiftOverride.user_id == user_id_for_wages, ShiftOverride.date == date_obj)
        .first()
    )
    if shift_override:
        from app.core.storage import load_shift_types

        all_shifts = load_shift_types()
        override_shift = next((s for s in all_shifts if s.code == shift_override.shift_code), None)
        if override_shift:
            shift = override_shift
            is_effective_oc = False  # Override takes priority over OC

    hours: float = 0.0
    start_dt: datetime | None = None
    end_dt: datetime | None = None

    if shift and shift.code != "OFF":
        hours, start_dt, end_dt = calculate_shift_hours(date_obj, shift)

    special_rules = _cached_special_rules(year)
    combined_rules = ob_rules + special_rules

    # Get person name from database
    if current_user.id == user_id_for_wages:
        person_name = current_user.name
    else:
        holder = db.query(User).filter(User.id == user_id_for_wages).first()
        if holder:
            person_name = holder.name
        else:
            person_name = person_list[rotation_position - 1].name

    # Use temporal wage query for the specific date being viewed
    # Use user_id_for_wages for wage lookup
    monthly_salary = get_effective_monthly_wage(db, user_id_for_wages, settings.monthly_salary, effective_date=date_obj)

    # Resolve per-user rates for the viewed user
    from app.core.rates import get_user_rates

    _rate_user = (
        db.query(User).filter(User.id == user_id_for_wages).first()
        if user_id_for_wages != current_user.id
        else current_user
    )
    _user_rates = (
        get_user_rates(_rate_user, session=db, effective_date=date_obj) if _rate_user else get_user_rates(current_user)
    )

    # OT shifts never have OB pay, so check if this will become an OT shift
    # We need to check this before fetching the OT shift
    # OT shifts are stored per user_id
    temp_ot_check = get_overtime_shift_for_date(db, user_id_for_wages, date_obj)
    # Extensions keep OB on scheduled hours; only full call-in OT removes OB
    is_full_ot = temp_ot_check and not temp_ot_check.is_extension

    # OC shifts also don't have OB - they have oncall pay instead
    if start_dt and end_dt and not is_full_ot and not is_effective_oc:
        ob_hours = calculate_ob_hours(start_dt, end_dt, combined_rules)
        ob_pay = calculate_ob_pay(start_dt, end_dt, combined_rules, monthly_salary, rate_overrides=_user_rates["ob"])
    else:
        # No OB for full OT shifts or OC shifts
        ob_hours = {r.code: 0.0 for r in ob_rules}
        ob_pay = {r.code: 0.0 for r in ob_rules}

    ob_codes = sorted(ob_hours.keys())
    weekday_name = weekday_names[date_obj.weekday()]

    midnight = datetime.combine(date_obj, time(0, 0))
    active_special_rules = _select_ob_rules_for_date(midnight, special_rules)

    # Fetch absence for this person and date
    # Absences are stored per user_id
    absence = db.query(Absence).filter(Absence.user_id == user_id_for_wages, Absence.date == date_obj).first()

    # Partial absence: truncate shift end/start and recalculate OB
    original_end_dt = end_dt
    if absence and absence.left_at and start_dt is not None and end_dt is not None:
        left_time = datetime.strptime(absence.left_at, "%H:%M").time()
        truncated_end = datetime.combine(date_obj, left_time)
        if truncated_end > start_dt:
            end_dt = truncated_end
            hours = (end_dt - start_dt).total_seconds() / 3600.0
            if not is_full_ot and not is_effective_oc:
                ob_hours = calculate_ob_hours(start_dt, end_dt, combined_rules)
                ob_pay = calculate_ob_pay(
                    start_dt, end_dt, combined_rules, monthly_salary, rate_overrides=_user_rates["ob"]
                )

    if absence and absence.arrived_at and start_dt is not None and end_dt is not None:
        arrived_time = datetime.strptime(absence.arrived_at, "%H:%M").time()
        truncated_start = datetime.combine(date_obj, arrived_time)
        if truncated_start < end_dt:
            start_dt = truncated_start
            hours = (end_dt - start_dt).total_seconds() / 3600.0
            if not is_full_ot and not is_effective_oc:
                ob_hours = calculate_ob_hours(start_dt, end_dt, combined_rules)
                ob_pay = calculate_ob_pay(
                    start_dt, end_dt, combined_rules, monthly_salary, rate_overrides=_user_rates["ob"]
                )

    # Full-day sick absence: zero out OB
    if (
        absence
        and absence.absence_type.value == "SICK"
        and absence.left_at is None
        and absence.arrived_at is None
        and not is_full_ot
        and not is_effective_oc
    ):
        ob_hours = {code: 0.0 for code in ob_hours}
        ob_pay = {code: 0.0 for code in ob_pay}

    ot_result = compute_ot_details(db, user_id_for_wages, date_obj, monthly_salary, _user_rates["ot"], absence=absence)
    ot_shift = ot_result["ot_shift"]
    ot_shift_id = ot_shift.id if ot_shift else None
    ot_details = ot_result["ot_details"]
    ot_shift_for_oncall = ot_result["ot_shift_for_oncall"]

    if ot_shift and not absence and not ot_shift.is_extension:
        from app.core.models import ShiftType
        from app.core.storage import load_shift_types

        ot_start_str = ot_details["start_time"]
        ot_end_str = ot_details["end_time"]
        all_shifts = load_shift_types()
        ot_shift_type = next((s for s in all_shifts if s.code == "OT"), None)
        if ot_shift_type:
            shift = ShiftType(
                code="OT",
                label=ot_shift_type.label,
                start_time=ot_start_str,
                end_time=ot_end_str,
                color=ot_shift_type.color,
            )
            hours = ot_shift.hours
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

    oncall_pay = 0.0
    oncall_details = {}
    if is_effective_oc:
        oc_result = compute_oncall_details(date_obj, year, monthly_salary, _user_rates["oncall"], ot_shift_for_oncall)
        oncall_pay = oc_result["oncall_pay"]
        oncall_details = oc_result["oncall_details"]

    # Apply manual hour overrides if one exists for this user and date
    day_pay_override = (
        db.query(DayPayOverride)
        .filter(DayPayOverride.user_id == user_id_for_wages, DayPayOverride.date == date_obj)
        .first()
    )
    if day_pay_override:
        if day_pay_override.ob_hours_override:
            ob_hours, ob_pay = apply_ob_hours_override(
                day_pay_override.ob_hours_override, monthly_salary, combined_rules, _user_rates["ob"]
            )
        if day_pay_override.oncall_hours_override:
            _oncall_rules = _get_oncall_rules(year)
            oncall_pay, oncall_details = apply_oncall_hours_override(
                day_pay_override.oncall_hours_override,
                oncall_details.get("breakdown", {}),
                monthly_salary,
                _oncall_rules,
                _user_rates["oncall"],
            )

    show_salary = can_see_salary(current_user, rotation_position)

    # Build deduplicated ordered list of all on-call type codes+labels for the override form
    _all_oc_rules = _get_oncall_rules(year)
    seen_oc_codes: set[str] = set()
    all_oncall_types: list[dict] = []
    for _r in sorted(_all_oc_rules, key=lambda r: r.priority):
        if _r.code not in seen_oc_codes:
            seen_oc_codes.add(_r.code)
            all_oncall_types.append({"code": _r.code, "label": _r.label})

    # Check if this date is a storhelg (major holiday)
    storhelg_dates = _get_storhelg_dates_for_year(year)
    is_storhelg = date_obj in storhelg_dates

    # Calculate absence deduction if absence exists
    absence_deduction = 0.0
    absence_shift_hours = 0.0
    is_karens = False
    karens_hours_today = 0.0
    sjuklon_hours_today = 0.0
    sick_ob_pay_today = 0.0

    if absence and show_salary:
        from app.core.schedule.wages import (
            KARENS_HOURS,
            calculate_absence_deduction,
            get_absent_hours_for_absence,
            get_karens_consumed_before_date,
            get_shift_times_for_date,
        )

        # Get shift hours and times for the day
        full_shift_hours, shift_start_dt, shift_end_dt = get_shift_times_for_date(db, rotation_position, date_obj)
        absent_hours = get_absent_hours_for_absence(absence, shift_start_dt, shift_end_dt, full_shift_hours)
        # absence_shift_hours visas i templaten
        absence_shift_hours = absent_hours

        if absence.absence_type.value == "SICK":
            karens_consumed = get_karens_consumed_before_date(db, user_id_for_wages, date_obj)
            karens_remaining = max(0.0, KARENS_HOURS - karens_consumed)
            karens_hours_today = min(absent_hours, karens_remaining)
            sjuklon_hours_today = absent_hours - karens_hours_today
            is_karens = karens_hours_today > 0
            absence_deduction = calculate_absence_deduction(
                monthly_salary,
                absence.absence_type.value,
                full_shift_hours,
                absent_hours=absent_hours,
                karens_remaining=karens_remaining,
            )

            # OB compensation for sick absence (80% of OB on sick-pay hours)
            if (
                _user_rates.get("sick", {}).get("ob_compensation")
                and sjuklon_hours_today > 0
                and start_dt is not None
                and original_end_dt is not None
                and full_shift_hours > 0
            ):
                from app.core.schedule.ob import calculate_ob_pay as _calc_ob_pay_sick

                full_shift_ob = _calc_ob_pay_sick(
                    start_dt, original_end_dt, combined_rules, monthly_salary, rate_overrides=_user_rates["ob"]
                )
                sick_ob_pay_today = sum(full_shift_ob.values()) * (sjuklon_hours_today / full_shift_hours) * 0.8
        else:
            is_karens = False
            karens_hours_today = 0.0
            sjuklon_hours_today = absent_hours
            absence_deduction = calculate_absence_deduction(
                monthly_salary, absence.absence_type.value, full_shift_hours, absent_hours=absent_hours
            )

    # Get coworkers for this day
    from app.core.schedule import generate_period_data
    from app.core.schedule.cowork import get_coworkers_for_day

    # Fetch all persons' data for this single day (include substitutes so they show as coworkers)
    all_persons_day = generate_period_data(date_obj, date_obj, person_id=None, session=db, include_substitutes=True)

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
        # Use actual_shift directly - if this person has a swap, actual_shift_obj
        # already reflects the swapped shift code.
        shift_code_for_matching = actual_shift_obj.code if actual_shift_obj else "OFF"

    # Use rotation_position for coworker matching (schedule-based)
    coworkers = get_coworkers_for_day(rotation_position, shift_code_for_matching, persons_today, start_dt, end_dt)

    # Check if this day is a vacation day for the user
    _vac_user = (
        db.query(User).filter(User.id == user_id_for_wages).first()
        if user_id_for_wages != current_user.id
        else current_user
    )
    is_vacation_day = False
    if _vac_user:
        _iso_year, _iso_week, _ = date_obj.isocalendar()
        _vac_json = _vac_user.vacation or {}
        if str(_iso_year) in _vac_json and _iso_week in _vac_json[str(_iso_year)]:
            is_vacation_day = True
        if not is_vacation_day:
            is_vacation_day = (
                db.query(Absence)
                .filter(
                    Absence.user_id == user_id_for_wages,
                    Absence.date == date_obj,
                    Absence.absence_type == AbsenceType.VACATION,
                )
                .first()
                is not None
            )

    return render_template(
        templates,
        "day.html",
        request,
        {
            "person_id": person_id,
            "person_name": person_name,
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
            "absence_deduction": absence_deduction,
            "absence_shift_hours": absence_shift_hours,
            "is_karens": is_karens,
            "karens_hours_today": karens_hours_today,
            "sjuklon_hours_today": sjuklon_hours_today,
            "sick_ob_pay_today": sick_ob_pay_today,
            "before_employment": before_employment,
            "coworkers": coworkers if not before_employment else [],
            "all_working_persons": persons_today_with_shift if not before_employment else [],
            "swap_users": db.query(User)
            .filter(User.is_active == 1, User.id != current_user.id, User.role != UserRole.ADMIN)
            .all(),
            "oncall_override": oncall_override,
            "has_rotation_oc": has_rotation_oc,
            "is_effective_oc": is_effective_oc,
            "shift_override": shift_override,
            "is_vacation_day": is_vacation_day,
            "day_pay_override": day_pay_override,
            "all_oncall_types": all_oncall_types,
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
    """Week view for a specific person.

    The person_id parameter can be:
    - 1-10: A rotation position (legacy, still supported)
    - > 10: A user_id (e.g., 11 for Rickard who has rotation position 3)
    """
    # Handle both user_id (>10) and rotation position (1-10)
    if person_id > 10:
        target_user = db.query(User).filter(User.id == person_id).first()
        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")

        rotation_position = target_user.rotation_person_id
        person_name = target_user.name
    else:
        person_id = validate_person_id(person_id)
        rotation_position = person_id
        pos_user = db.query(User).filter(User.person_id == rotation_position, User.is_active == 1).first()
        person_name = pos_user.name if pos_user else None

    safe_today = get_safe_today(rotation_start_date)
    iso_year, iso_week, _ = safe_today.isocalendar()

    year = year or iso_year
    week = week or iso_week

    # Use rotation_position for schedule calculation
    # For user_id lookups, pass employment start so before-employment days show correctly
    week_employment_start = None
    if person_id > 10:
        from app.core.schedule.person_history import get_employment_period

        week_emp_start, _ = get_employment_period(db, target_user.id, rotation_position)
        week_employment_start = week_emp_start

    days_in_week = build_week_data(
        year,
        week,
        person_id=rotation_position,
        session=db,
        include_coworkers=True,
        employment_start=week_employment_start,
    )

    monday = date.fromisocalendar(year, week, 1)
    nav = get_navigation_dates("week", monday)

    real_today = get_today()

    storhelg_dates = _get_storhelg_dates_for_year(year)
    holiday_dates = get_holiday_dates_for_year(year)

    return render_template(
        templates,
        "week.html",
        request,
        {
            "year": year,
            "week": week,
            "days": days_in_week,
            "person_id": person_id,
            "person_name": person_name,
            "today": real_today,
            "storhelg_dates": storhelg_dates,
            "holiday_dates": holiday_dates,
            **nav,
        },
        user=current_user,
    )


@router.get("/range/{person_id}", response_class=HTMLResponse, name="range_person")
async def show_range_for_person(
    request: Request,
    person_id: int,
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
    weeks_param: int | None = Query(None, alias="weeks", ge=1, le=10),
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Range view for a specific person -- arbitrary date interval (max 70 days)."""
    if current_user is None:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)

    if person_id > 10:
        target_user = db.query(User).filter(User.id == person_id).first()
        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")
        user_id_for_wages = person_id
        rotation_position = target_user.rotation_person_id
        person_name = target_user.name
    else:
        person_id = validate_person_id(person_id)
        user_id_for_wages = person_id
        rotation_position = person_id
        person_name = None

    if redirect := redirect_if_not_own_data(current_user, user_id_for_wages, f"/range/{current_user.id}"):
        return redirect

    real_today = get_today()

    # Weeks-based mode: snap to Monday, compute end from weeks count
    if weeks_param is not None or (from_date is None and to_date is None):
        active_weeks = weeks_param if weeks_param is not None else 2
        try:
            anchor = date.fromisoformat(from_date) if from_date else real_today
        except ValueError:
            anchor = real_today
        start = anchor - timedelta(days=anchor.weekday())  # snap to Monday
        end = start + timedelta(weeks=active_weeks) - timedelta(days=1)
    else:
        # Free-form mode: both from/to provided explicitly
        active_weeks = None
        try:
            start = date.fromisoformat(from_date) if from_date else real_today
            end = date.fromisoformat(to_date) if to_date else real_today + timedelta(days=13)
        except ValueError:
            start = real_today
            end = real_today + timedelta(days=13)
        if end < start:
            end = start
        if (end - start).days >= 70:
            end = start + timedelta(days=69)

    if person_name is None:
        if current_user is not None and current_user.rotation_person_id == rotation_position:
            person_name = current_user.name
        else:
            holder = db.query(User).filter(User.person_id == rotation_position).first()
            person_name = holder.name if holder else person_list[rotation_position - 1].name

    range_employment_start = None
    if person_id > 10:
        from app.core.schedule.person_history import get_employment_period

        emp_start, _ = get_employment_period(db, target_user.id, rotation_position)
        range_employment_start = emp_start

    # Build days week-by-week then filter to exact range (reuses build_week_data incl. coworkers)
    days_in_range = []
    current_monday = start - timedelta(days=start.weekday())
    seen_weeks: set[tuple[int, int]] = set()
    while current_monday <= end:
        iso_year, iso_week, _ = current_monday.isocalendar()
        if (iso_year, iso_week) not in seen_weeks:
            seen_weeks.add((iso_year, iso_week))
            week_days = build_week_data(
                iso_year,
                iso_week,
                person_id=rotation_position,
                session=db,
                include_coworkers=True,
                employment_start=range_employment_start,
            )
            for d in week_days:
                if start <= d["date"] <= end:
                    days_in_range.append(d)
        current_monday += timedelta(days=7)

    years_in_range = {d["date"].year for d in days_in_range}
    storhelg_dates: set = set()
    holiday_dates: set = set()
    for yr in years_in_range:
        storhelg_dates |= _get_storhelg_dates_for_year(yr)
        holiday_dates |= get_holiday_dates_for_year(yr)

    return render_template(
        templates,
        "range.html",
        request,
        {
            "person_id": person_id,
            "person_name": person_name,
            "start_date": start,
            "end_date": end,
            "days": days_in_range,
            "num_weeks": len(seen_weeks),
            "active_weeks": active_weeks,
            "prev_from": (start - timedelta(weeks=active_weeks)).isoformat() if active_weeks else None,
            "next_from": (start + timedelta(weeks=active_weeks)).isoformat() if active_weeks else None,
            "prev_week_from": (start - timedelta(weeks=1)).isoformat() if active_weeks else None,
            "next_week_from": (start + timedelta(weeks=1)).isoformat() if active_weeks else None,
            "today": real_today,
            "storhelg_dates": storhelg_dates,
            "holiday_dates": holiday_dates,
        },
        user=current_user,
    )


def _compute_sjuklon_base(
    hourly_rate: float,
    sick_hours: float,
    absence_deduction: float,
    sick_ob_hours_by_code: dict,
) -> dict:
    sjuklon_pay_total = max(0.0, hourly_rate * sick_hours - absence_deduction)
    sjuklon_hours_total = (sjuklon_pay_total / (hourly_rate * 0.8)) if hourly_rate > 0 else 0.0
    sick_ob_h_total = sum(sick_ob_hours_by_code.values())
    sjuklon_base_hours = max(0.0, sjuklon_hours_total - sick_ob_h_total)
    return {
        "sjuklon_base_hours": sjuklon_base_hours,
        "sjuklon_base_pay": sjuklon_base_hours * hourly_rate * 0.8,
    }


@router.get("/month/{person_id}", response_class=HTMLResponse, name="month_person")
async def show_month_for_person(
    request: Request,
    person_id: int,
    year: int = None,
    month: int = None,
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Month view for a specific person.

    The person_id parameter can be:
    - 1-10: A rotation position (legacy, still supported)
    - > 10: A user_id (e.g., 11 for Rickard who has rotation position 3)
    """
    start_time = datetime.now()

    # Handle both user_id (>10) and rotation position (1-10)
    if person_id > 10:
        # It's a user_id, look up the user
        target_user = db.query(User).filter(User.id == person_id).first()
        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")

        user_id_for_wages = person_id
        rotation_position = target_user.rotation_person_id
        person_name = target_user.name
    else:
        person_id = validate_person_id(person_id)
        user_id_for_wages = person_id
        rotation_position = person_id
        person_name = None

    safe_today = get_safe_today(rotation_start_date)

    year = year or safe_today.year
    month = month or safe_today.month

    validate_date_params(year, month, None)

    # Get person name if not already set
    if person_name is None:
        if current_user is not None and current_user.rotation_person_id == rotation_position:
            person_name = current_user.name
        else:
            holder = db.query(User).filter(User.person_id == rotation_position).first()
            if holder:
                person_name = holder.name
            else:
                holder = db.query(User).filter(User.id == rotation_position).first()
                person_name = holder.name if holder else person_list[rotation_position - 1].name

    # Use rotation_position for schedule calculation
    # For user_id lookups, pass the user's employment start so dates before it show as before_employment
    viewer_employment_start = None
    if person_id > 10:
        from app.core.schedule.person_history import get_employment_period

        emp_start, _ = get_employment_period(db, target_user.id, rotation_position)
        viewer_employment_start = emp_start

    calendar_data = build_calendar_grid_for_month(
        year,
        month,
        person_id=rotation_position,
        session=db,
        include_coworkers=True,
        employment_start=viewer_employment_start,
    )
    days_in_month = calendar_data["summary"]
    calendar_grid = calendar_data["grid"]

    show_salary = can_see_salary(current_user, rotation_position)

    if not show_salary:
        days_in_month = strip_salary_data(days_in_month)

    # Calculate and log load time
    end_time = datetime.now()
    load_time = (end_time - start_time).total_seconds()
    logger.info(
        f"Route /month/{person_id} (year={year}, month={month}, "
        f"rotation={rotation_position}) loaded in {load_time:.3f}s",
        extra={
            "duration_ms": load_time * 1000,
            "path": f"/month/{person_id}",
            "user_id": current_user.id if current_user else None,
        },
    )

    storhelg_dates = _get_storhelg_dates_for_year(year)
    holiday_dates = get_holiday_dates_for_year(year)

    # Count vacation days (SEM shifts) in this month and calculate supplement
    vacation_month = None
    if show_salary:
        sem_count = sum(1 for d in days_in_month.get("days", []) if d.get("shift") and d["shift"].code == "SEM")
        if sem_count > 0:
            vac_user = (
                db.query(User).filter(User.id == user_id_for_wages).first()
                if user_id_for_wages > 10
                else db.query(User).filter(User.person_id == rotation_position).first()
            )
            if vac_user:
                try:
                    balance = calculate_vacation_balance(vac_user, year, db)
                    pay = balance.get("pay", {})
                    vacation_month = {
                        "days": sem_count,
                        "supplement_per_day": pay.get("supplement_per_day", 0),
                        "supplement_month": round(pay.get("supplement_per_day", 0) * sem_count, 0),
                    }
                except Exception:
                    logger.warning(
                        "Semestertillägg kunde inte beräknas för user_id=%s", user_id_for_wages, exc_info=True
                    )

    # Hide summary stats if the entire month is before the viewer's employment start
    import calendar as _cal_mod
    from datetime import date as _date

    before_employment_month = viewer_employment_start is not None and viewer_employment_start > _date(
        year, month, _cal_mod.monthrange(year, month)[1]
    )

    # Aggregated payslip-style breakdown for hourly wage users
    hourly_breakdown = None
    if show_salary:
        from app.database.database import WageType

        wage_user = (
            db.query(User).filter(User.id == user_id_for_wages).first()
            if user_id_for_wages > 10
            else db.query(User).filter(User.person_id == rotation_position).first()
        )
        if wage_user and getattr(wage_user, "wage_type", None) == WageType.HOURLY:
            hourly_rate = float(
                get_user_wage(db, user_id_for_wages, settings.monthly_salary, effective_date=date(year, month, 1))
            )
            _OC_TO_GROUP = {
                "OC_WEEKDAY": "oc_vardag",
                "OC_WEEKEND": "oc_helg",
                "OC_WEEKEND_SAT": "oc_helg",
                "OC_WEEKEND_SUN": "oc_helg",
                "OC_WEEKEND_MON": "oc_helg",
                "OC_HOLIDAY": "oc_helgdag",
                "OC_HOLIDAY_EVE": "oc_helgdag",
                "OC_NATIONALDAGEN": "oc_helgdag",
                "OC_SPECIAL": "oc_storhelg",
            }
            agg = {
                k: {"hours": 0.0, "pay": 0.0}
                for k in [
                    "norm",
                    "OB1",
                    "OB2",
                    "OB3",
                    "OB4",
                    "OB5",
                    "oc_vardag",
                    "oc_helg",
                    "oc_helgdag",
                    "oc_storhelg",
                    "ot",
                ]
            }
            for d in days_in_month.get("days", []):
                shift = d.get("shift")
                hours = d.get("hours", 0.0) or 0.0
                ob_h = d.get("ob_hours", {}) or {}
                ob_p = d.get("ob_pay", {}) or {}
                if shift and shift.code not in ("OFF", "OC", "OT") and hours:
                    ob_sum = sum(ob_h.values())
                    norm = max(hours - ob_sum, 0.0)
                    agg["norm"]["hours"] += norm
                    agg["norm"]["pay"] += norm * hourly_rate
                    for code in ("OB1", "OB2", "OB3", "OB4", "OB5"):
                        h = ob_h.get(code, 0.0) or 0.0
                        agg[code]["hours"] += h
                        agg[code]["pay"] += (ob_p.get(code, 0.0) or 0.0) + h * hourly_rate
                oc_bd = (d.get("oncall_details") or {}).get("breakdown", {}) or {}
                for oc_code, group in _OC_TO_GROUP.items():
                    entry = oc_bd.get(oc_code) or {}
                    agg[group]["hours"] += entry.get("hours", 0.0) or 0.0
                    agg[group]["pay"] += entry.get("pay", 0.0) or 0.0
                agg["ot"]["hours"] += d.get("ot_hours", 0.0) or 0.0
                agg["ot"]["pay"] += d.get("ot_pay", 0.0) or 0.0
            last_day = _cal_mod.monthrange(year, month)[1]
            _sick_ob_py = days_in_month.get("sick_ob_pay_by_code", {}) or {}
            _sick_ob_hs = days_in_month.get("sick_ob_hours_by_code", {}) or {}
            _sjuklon_info = _compute_sjuklon_base(
                hourly_rate,
                days_in_month.get("sick_hours", 0.0) or 0.0,
                days_in_month.get("absence_deduction", 0.0) or 0.0,
                _sick_ob_hs,
            )
            hourly_breakdown = {
                "hourly_rate": hourly_rate,
                "period": f"{year}{month:02d}01-{year}{month:02d}{last_day:02d}",
                "rows": agg,
                "sick_days": days_in_month.get("sick_days", 0) or 0,
                "sick_ob_pay_by_code": _sick_ob_py,
                "sick_ob_hours_by_code": _sick_ob_hs,
                **_sjuklon_info,
            }

    return render_template(
        templates,
        "month.html",
        request,
        {
            "year": year,
            "month": month,
            "person_id": person_id,
            "person_name": person_name,
            "days": days_in_month,
            "calendar_grid": calendar_grid,
            "show_salary": show_salary,
            "storhelg_dates": storhelg_dates,
            "holiday_dates": holiday_dates,
            "vacation_month": vacation_month,
            "before_employment_month": before_employment_month,
            "hourly_breakdown": hourly_breakdown,
            "today": get_today(),
        },
        user=current_user,
    )


@router.get("/month/{person_id}/export-excel", name="month_export_excel")
async def export_month_excel(
    request: Request,
    person_id: int,
    year: int = None,
    month: int = None,
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Export monthly data as an Excel file."""
    import openpyxl

    from app.core.schedule.summary import summarize_month_for_person
    from app.routes.excel_shared import populate_month_sheet

    if current_user is None:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)

    # Resolve person_id -> rotation_position (same logic as show_month_for_person)
    if person_id > 10:
        target_user = db.query(User).filter(User.id == person_id).first()
        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")
        user_id_for_wages = person_id
        rotation_position = target_user.rotation_person_id
        person_name = target_user.name
    else:
        person_id = validate_person_id(person_id)
        user_id_for_wages = person_id
        rotation_position = person_id
        person_name = None

    safe_today = get_safe_today(rotation_start_date)
    year = year or safe_today.year
    month = month or safe_today.month

    if redirect := redirect_if_not_own_data(
        current_user, user_id_for_wages, f"/month/{current_user.id}?year={year}&month={month}"
    ):
        return redirect

    if not can_see_salary(current_user, rotation_position):
        raise HTTPException(status_code=403, detail="Åtkomst nekad")

    validate_date_params(year, month, None)

    days_in_month = summarize_month_for_person(year, month, rotation_position, session=db, payment_year=year)

    # ── Build workbook ───────────────────────────────────────────────────
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"{year}-{month:02d}"
    populate_month_sheet(ws, days_in_month, year, month)

    # ── Stream response ──────────────────────────────────────────────────
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"schema_{person_name or rotation_position}_{year}-{month:02d}.xlsx"
    filename_safe = filename.replace(" ", "_")

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename_safe}"'},
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
    """Year view for a specific person.

    The person_id parameter can be:
    - 1-10: A rotation position (legacy, still supported)
    - > 10: A user_id (e.g., 11 for Rickard who has rotation position 3)

    When person_id > 10, we look up the user's rotation_person_id for schedule
    calculation but use the original user_id for wage lookup.
    """
    start_time = datetime.now()

    if current_user is None:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)

    # Handle both user_id (>10) and rotation position (1-10)
    if person_id > 10:
        # It's a user_id, look up the user
        target_user = db.query(User).filter(User.id == person_id).first()
        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")

        user_id_for_wages = person_id  # Use for wage lookup
        rotation_position = target_user.rotation_person_id  # Use for schedule
        person_name = target_user.name
    else:
        # It's a rotation position (1-10)
        person_id = validate_person_id(person_id)
        user_id_for_wages = person_id  # Same as person_id for legacy users
        rotation_position = person_id
        person_name = None  # Will be looked up below

    if with_person_id is not None:
        with_person_id = validate_person_id(with_person_id)

    safe_today = get_safe_today(rotation_start_date)
    year = year or safe_today.year

    # Get person name if not already set (for user_id > 10 case, it's set above)
    if person_name is None:
        if current_user.rotation_person_id == rotation_position:
            # User viewing their own position
            person_name = current_user.name
        else:
            # Admin viewing someone else's position - find current holder
            holder = db.query(User).filter(User.person_id == rotation_position).first()
            if holder:
                person_name = holder.name
            else:
                # Fallback: legacy user where user_id == person_id
                holder = db.query(User).filter(User.id == rotation_position).first()
                person_name = holder.name if holder else person_list[rotation_position - 1].name

    # Use rotation_position for schedule-related calculations
    cowork_rows = build_cowork_stats(year, rotation_position)
    selected_other_id = None
    selected_other_name = None
    cowork_details: list[dict] = []

    if with_person_id:
        selected_other_id = with_person_id
        _other_row = next((r for r in cowork_rows if r["other_id"] == with_person_id), None)
        selected_other_name = _other_row["other_name"] if _other_row else str(with_person_id)
        cowork_details = build_cowork_details(year, rotation_position, with_person_id)

    # Use rotation_position for schedule, user_id_for_wages for wage lookup
    year_data = summarize_year_for_person(
        year, rotation_position, session=db, current_user=current_user, wage_user_id=user_id_for_wages
    )
    months = year_data["months"]
    year_summary = year_data["year_summary"]

    # Get OB rules for label lookup
    special_rules = _cached_special_rules(year)
    combined_rules = ob_rules + special_rules

    show_salary = can_see_salary(current_user, rotation_position)

    if not show_salary:
        months = [strip_salary_data(m) for m in months]
        year_summary = strip_salary_data(year_summary)

    # Calculate vacation supplement per month if salary is visible
    vacation_pay = None
    if show_salary:
        vac_user = (
            db.query(User).filter(User.id == user_id_for_wages).first()
            if user_id_for_wages > 10
            else db.query(User).filter(User.person_id == rotation_position).first()
        )
        if vac_user:
            try:
                vacation_pay = calculate_vacation_balance(vac_user, year, db)
                supp_per_day = vacation_pay.get("pay", {}).get("supplement_per_day", 0)
                total_sem_days = 0
                total_supplement = 0.0
                for m in months:
                    sem_days = sum(1 for d in m.get("days", []) if d.get("shift") and d["shift"].code == "SEM")
                    m["vacation_days"] = sem_days
                    m["vacation_supplement"] = round(supp_per_day * sem_days, 0)
                    total_sem_days += sem_days
                    total_supplement += m["vacation_supplement"]

                    # Include supplement in gross/net so table columns add up
                    if m["vacation_supplement"] > 0:
                        supp = m["vacation_supplement"]
                        brutto_before = m.get("brutto_pay", 0) or 0
                        netto_before = m.get("netto_pay", 0) or 0
                        m["brutto_pay"] = brutto_before + supp
                        if brutto_before > 0:
                            tax_ratio = netto_before / brutto_before
                            m["netto_pay"] = round(netto_before + supp * tax_ratio, 0)

                # Recalculate year totals with updated brutto/netto
                month_count = len(months) or 1
                year_summary["total_brutto"] = sum((m.get("brutto_pay", 0) or 0) for m in months)
                year_summary["total_netto"] = sum((m.get("netto_pay", 0) or 0) for m in months)
                year_summary["avg_brutto"] = round(year_summary["total_brutto"] / month_count, 0)
                year_summary["avg_netto"] = round(year_summary["total_netto"] / month_count, 0)
                year_summary["total_vacation_days"] = total_sem_days
                year_summary["total_vacation_supplement"] = total_supplement
                year_summary["avg_vacation_supplement"] = round(total_supplement / month_count, 0)
            except Exception:
                pass

    # Check for employment transition in this year
    transition_data = None
    if show_salary:
        t_user = (
            db.query(User).filter(User.id == user_id_for_wages).first()
            if user_id_for_wages > 10
            else db.query(User).filter(User.person_id == rotation_position).first()
        )
        if t_user and t_user.employment_transition:
            t = t_user.employment_transition
            if t.transition_date.year == year:
                try:
                    from app.core.schedule.transition import calculate_transition_month_summary

                    transition_data = calculate_transition_month_summary(t, t_user, db)
                except Exception:
                    pass

    # Inject employment transition into months list
    if transition_data and show_salary:
        vac_payout = float(transition_data["consultant_employer"]["vacation_payout"]["total"])
        direct_salary = float(transition_data["direct_employer"]["base_salary"])
        t_year = transition_data["transition_year"]
        t_month = transition_data["transition_month"]

        for i, m in enumerate(months):
            if m["payment_date"].year == t_year and m["payment_date"].month == t_month:
                brutto = float(m.get("brutto_pay") or 0)
                netto = float(m.get("netto_pay") or 0)
                tax_ratio = (netto / brutto) if brutto > 0 else 0.72

                # Add vacation payout to Sem.till. and Gross on the trailing consultant row
                m["vacation_supplement"] = round((m.get("vacation_supplement") or 0) + vac_payout, 0)
                m["brutto_pay"] = round(brutto + vac_payout, 0)
                m["netto_pay"] = round(netto + vac_payout * tax_ratio, 0)

                # Extra row: direct employer innestående base salary
                original_count = len(months)  # before insert
                months.insert(
                    i + 1,
                    {
                        "payment_date": m["payment_date"],
                        "year": t_year,
                        "month": t_month,
                        "transition_direct": True,
                        "netto_pay": round(direct_salary * tax_ratio, 0),
                        "brutto_pay": direct_salary,
                        "num_shifts": 0,
                        "total_hours": 0,
                        "total_ob": 0,
                        "oncall_pay": 0,
                        "ot_pay": 0,
                        "absence_deduction": 0,
                        "vacation_supplement": 0,
                    },
                )

                # Update year totals and averages — average uses original month count
                year_summary["total_brutto"] = sum(float(m2.get("brutto_pay") or 0) for m2 in months)
                year_summary["total_netto"] = sum(float(m2.get("netto_pay") or 0) for m2 in months)
                year_summary["avg_brutto"] = round(year_summary["total_brutto"] / original_count, 0)
                year_summary["avg_netto"] = round(year_summary["total_netto"] / original_count, 0)
                if "total_vacation_supplement" in year_summary:
                    year_summary["total_vacation_supplement"] = sum(
                        float(m2.get("vacation_supplement") or 0) for m2 in months
                    )
                    year_summary["avg_vacation_supplement"] = round(
                        year_summary["total_vacation_supplement"] / original_count, 0
                    )
                break

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
            "person_name": person_name,
            "months": months,
            "year_summary": year_summary,
            "cowork_rows": cowork_rows,
            "cowork_details": cowork_details,
            "selected_other_id": selected_other_id,
            "selected_other_name": selected_other_name,
            "show_salary": show_salary,
            "ob_rules": combined_rules,  # All OB rules for label lookup
            "vacation_pay": vacation_pay,
        },
        user=current_user,
    )


@router.get("/cowork/{person_id}", response_class=HTMLResponse, name="cowork_person")
async def cowork_view(
    request: Request,
    person_id: int,
    year: int = Query(None),
    with_person_id: int | None = Query(None, alias="with_person_id"),
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Dedicated page for co-work statistics for a specific person."""
    if current_user is None:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)

    # Hantera user_id (>10) vs rotationsposition (1-10)
    if person_id > 10:
        target_user = db.query(User).filter(User.id == person_id).first()
        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")
        user_id_for_wages = person_id
        rotation_position = target_user.rotation_person_id
        person_name = target_user.name
    else:
        person_id = validate_person_id(person_id)
        user_id_for_wages = person_id
        rotation_position = person_id
        person_name = None

    if redirect := redirect_if_not_own_data(
        current_user, user_id_for_wages, f"/cowork/{current_user.id}?year={year or ''}"
    ):
        return redirect

    if with_person_id is not None:
        with_person_id = validate_person_id(with_person_id)

    safe_today = get_safe_today(rotation_start_date)
    year = year or safe_today.year

    if person_name is None:
        if current_user.rotation_person_id == rotation_position:
            person_name = current_user.name
        else:
            holder = db.query(User).filter(User.person_id == rotation_position).first()
            if holder:
                person_name = holder.name
            else:
                holder = db.query(User).filter(User.id == rotation_position).first()
                person_name = holder.name if holder else person_list[rotation_position - 1].name

    cowork_rows = build_cowork_stats(year, rotation_position)

    selected_other_id = None
    selected_other_name = None
    selected_cowork_row = None
    cowork_details: list[dict] = []
    handover_details: list[dict] = []

    if with_person_id:
        selected_other_id = with_person_id
        selected_cowork_row = next((r for r in cowork_rows if r["other_id"] == with_person_id), None)
        selected_other_name = selected_cowork_row["other_name"] if selected_cowork_row else str(with_person_id)
        cowork_details = build_cowork_details(year, rotation_position, with_person_id)
        handover_details = build_handover_details(year, rotation_position, with_person_id)

    return render_template(
        templates,
        "cowork.html",
        request,
        {
            "year": year,
            "person_id": person_id,
            "person_name": person_name,
            "cowork_rows": cowork_rows,
            "cowork_details": cowork_details,
            "handover_details": handover_details,
            "selected_other_id": selected_other_id,
            "selected_other_name": selected_other_name,
            "selected_cowork_row": selected_cowork_row,
        },
        user=current_user,
    )
