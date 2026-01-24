# app/routes/dashboard.py
"""
Dashboard route - personalized home page for authenticated users.
"""

import datetime as dt
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user_optional
from app.core.helpers import can_see_salary, render_template
from app.core.oncall import _cached_oncall_rules, calculate_oncall_pay
from app.core.schedule import (
    _cached_special_rules,
    build_week_data,
    calculate_ob_hours,
    calculate_ob_pay,
    calculate_overtime_pay,
    calculate_shift_hours,
    get_overtime_shifts_for_month,
    get_user_wage,
    ob_rules,
    rotation_start_date,
)
from app.core.utils import get_safe_today, get_today
from app.database.database import Absence, OnCallOverride, OnCallOverrideType, User, get_db
from app.routes.shared import templates

router = APIRouter(tags=["dashboard"])


@router.get("/", response_class=HTMLResponse)
async def read_root(
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Home page - personalized dashboard for authenticated users."""
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    # Get current date and week (use safe_today to handle dates before rotation start)
    today = get_today()
    safe_today = get_safe_today(rotation_start_date)
    iso_year, iso_week, _ = safe_today.isocalendar()

    # Build this week's data for the user (pass session for oncall override support)
    week_data = build_week_data(iso_year, iso_week, person_id=current_user.id, session=db)

    # Batch fetch overtime shifts for current and next month to avoid N+1 queries
    current_month = safe_today.month
    current_year_num = safe_today.year
    next_month = current_month + 1 if current_month < 12 else 1
    next_month_year = current_year_num if current_month < 12 else current_year_num + 1

    ot_shifts_current = get_overtime_shifts_for_month(db, current_user.id, current_year_num, current_month)
    ot_shifts_next = get_overtime_shifts_for_month(db, current_user.id, next_month_year, next_month)

    # Create lookup dictionary for O(1) access
    ot_shift_map = {shift.date: shift for shift in ot_shifts_current + ot_shifts_next}

    # Find next upcoming shift (including overtime shifts)
    next_shift = None
    next_oncall_shift = None

    # Check up to 11 weeks ahead for upcoming shifts (covers full rotation cycle)
    weeks_to_check = []
    for week_offset in range(11):
        check_date = safe_today + dt.timedelta(days=7 * week_offset)
        weeks_to_check.append((check_date.isocalendar()[0], check_date.isocalendar()[1]))

    for check_year, check_week in weeks_to_check:
        # Stop searching if we found both types of shifts
        if next_shift and next_oncall_shift:
            break

        check_week_data = build_week_data(check_year, check_week, person_id=current_user.id, session=db)

        for day in check_week_data:
            if day["date"] < safe_today:
                continue

            # Check for overtime shift first (use dictionary lookup instead of DB query)
            ot_shift = ot_shift_map.get(day["date"])

            if ot_shift and not next_shift:
                # Show OT shift as next shift
                days_until = (day["date"] - today).days
                start_str = ot_shift.start_time.strftime("%H:%M")
                end_str = ot_shift.end_time.strftime("%H:%M")
                next_shift = {
                    "date": day["date"],
                    "shift_type": "OT (Overtime)",
                    "color": "#ff9800",  # Orange color for OT
                    "time_range": f"{start_str} - {end_str}",
                    "days_until": days_until,
                }
            # Check for regular rotation shift (excluding on-call)
            elif day["shift"] and day["shift"].code != "OFF" and day["shift"].code != "OC" and not next_shift:
                days_until = (day["date"] - today).days
                shift = day["shift"]
                time_range = f"{shift.start_time} - {shift.end_time}" if shift.start_time and shift.end_time else ""
                next_shift = {
                    "date": day["date"],
                    "shift_type": shift.label or shift.code,
                    "color": shift.color or "#666",
                    "time_range": time_range,
                    "days_until": days_until,
                }
            # Check for on-call shift separately
            elif day["shift"] and day["shift"].code == "OC" and not next_oncall_shift:
                # Store next on-call shift separately
                days_until = (day["date"] - today).days
                shift = day["shift"]
                time_range = f"{shift.start_time} - {shift.end_time}" if shift.start_time and shift.end_time else ""
                next_oncall_shift = {
                    "date": day["date"],
                    "shift_type": shift.label or shift.code,
                    "color": shift.color or "#666",
                    "time_range": time_range,
                    "days_until": days_until,
                }

    # Calculate week summary
    week_summary = None
    if week_data:
        total_hours = 0.0
        ob_hours = 0.0
        total_pay = 0.0
        oc_pay = 0.0
        ot_pay = 0.0
        absence_deduction = 0.0

        # Get OB rules for the year
        special_rules = _cached_special_rules(safe_today.year)
        combined_rules = ob_rules + special_rules

        # Get on-call rules for the year
        oncall_rules = _cached_oncall_rules(safe_today.year)

        # Fetch user wage once to avoid repeated queries
        user_wage = get_user_wage(db, current_user.id)

        for day in week_data:
            # Check for absence first
            absence = db.query(Absence).filter(Absence.user_id == current_user.id, Absence.date == day["date"]).first()

            if absence and can_see_salary(current_user, current_user.id):
                from app.core.schedule.wages import calculate_absence_deduction, get_shift_hours_for_date

                shift_hours = get_shift_hours_for_date(db, current_user.id, day["date"])

                # Check if karensdag
                five_days_ago = day["date"] - timedelta(days=5)
                previous_sick = None
                if absence.absence_type.value == "SICK":
                    previous_sick = (
                        db.query(Absence)
                        .filter(
                            Absence.user_id == current_user.id,
                            Absence.date >= five_days_ago,
                            Absence.date < day["date"],
                            Absence.absence_type == absence.absence_type,
                        )
                        .first()
                    )

                is_karens = previous_sick is None if absence.absence_type.value == "SICK" else False
                deduction = calculate_absence_deduction(user_wage, absence.absence_type.value, shift_hours, is_karens)
                absence_deduction += deduction

            # Check for overtime shift first (use dictionary lookup)
            ot_shift = ot_shift_map.get(day["date"])

            if ot_shift and not absence:  # Skip OT if there's an absence
                # Calculate OT hours and pay
                ot_start = datetime.combine(day["date"], ot_shift.start_time)
                ot_end = datetime.combine(day["date"], ot_shift.end_time)

                # Handle shifts that cross midnight
                if ot_shift.end_time < ot_shift.start_time:
                    ot_end += dt.timedelta(days=1)

                ot_hours = (ot_end - ot_start).total_seconds() / 3600.0
                total_hours += ot_hours

                if can_see_salary(current_user, current_user.id):
                    ot_ob_pay = calculate_overtime_pay(user_wage, ot_hours)
                    ot_pay += ot_ob_pay

            # Handle regular rotation shifts
            if day["shift"]:
                if day["shift"].code == "OC":
                    # Calculate on-call pay
                    if can_see_salary(current_user, current_user.id):
                        oc_result = calculate_oncall_pay(day["date"], user_wage, oncall_rules)
                        oc_pay += oc_result["total_pay"]

                elif day["shift"].code != "OFF":
                    # Calculate regular shift hours
                    hours, start_dt, end_dt = calculate_shift_hours(day["date"], day["shift"])
                    total_hours += hours

                    # Calculate OB hours and pay if we have valid datetimes
                    if start_dt and end_dt:
                        ob_hours_dict = calculate_ob_hours(start_dt, end_dt, combined_rules)
                        ob_hours += sum(ob_hours_dict.values())

                        if can_see_salary(current_user, current_user.id):
                            ob_pay_dict = calculate_ob_pay(start_dt, end_dt, combined_rules, user_wage)
                            total_pay += sum(ob_pay_dict.values())

        week_summary = {
            "total_hours": total_hours,
            "ob_hours": ob_hours,
            "total_pay": total_pay,
            "oc_pay": oc_pay,
            "ot_pay": ot_pay,
            "absence_deduction": absence_deduction,
        }

    # Calculate month summary
    month_summary = None
    current_month_start = safe_today.replace(day=1)
    if safe_today.month == 12:
        current_month_end = safe_today.replace(day=31)
    else:
        next_month_date = safe_today.replace(month=safe_today.month + 1, day=1)
        current_month_end = next_month_date - dt.timedelta(days=1)

    month_total_hours = 0.0
    month_ob_hours = 0.0
    month_total_pay = 0.0
    month_oc_pay = 0.0
    month_ot_pay = 0.0
    month_absence_deduction = 0.0

    # Get OB and oncall rules for month calculation
    special_rules = _cached_special_rules(safe_today.year)
    combined_rules = ob_rules + special_rules
    oncall_rules = _cached_oncall_rules(safe_today.year)
    user_wage = get_user_wage(db, current_user.id)

    # Batch fetch oncall overrides for the month
    month_oncall_overrides = (
        db.query(OnCallOverride)
        .filter(
            OnCallOverride.user_id == current_user.id,
            OnCallOverride.date >= current_month_start,
            OnCallOverride.date <= current_month_end,
        )
        .all()
    )
    oncall_override_map = {override.date: override for override in month_oncall_overrides}

    current_date = current_month_start
    while current_date <= current_month_end:
        # Check for absence first
        absence = db.query(Absence).filter(Absence.user_id == current_user.id, Absence.date == current_date).first()

        if absence and can_see_salary(current_user, current_user.id):
            from app.core.schedule import determine_shift_for_date
            from app.core.schedule.wages import calculate_absence_deduction, get_shift_hours_for_date

            shift_hours = get_shift_hours_for_date(db, current_user.id, current_date)

            # Check if karensdag
            five_days_ago = current_date - timedelta(days=5)
            previous_sick = None
            if absence.absence_type.value == "SICK":
                previous_sick = (
                    db.query(Absence)
                    .filter(
                        Absence.user_id == current_user.id,
                        Absence.date >= five_days_ago,
                        Absence.date < current_date,
                        Absence.absence_type == absence.absence_type,
                    )
                    .first()
                )

            is_karens = previous_sick is None if absence.absence_type.value == "SICK" else False
            deduction = calculate_absence_deduction(user_wage, absence.absence_type.value, shift_hours, is_karens)
            month_absence_deduction += deduction

        # Check for overtime shift first (use dictionary lookup)
        ot_shift = ot_shift_map.get(current_date)

        if ot_shift and not absence:  # Skip OT if there's an absence
            # Calculate OT hours and pay
            ot_start = datetime.combine(current_date, ot_shift.start_time)
            ot_end = datetime.combine(current_date, ot_shift.end_time)

            # Handle shifts that cross midnight
            if ot_shift.end_time < ot_shift.start_time:
                ot_end += dt.timedelta(days=1)

            ot_hours = (ot_end - ot_start).total_seconds() / 3600.0
            month_total_hours += ot_hours

            if can_see_salary(current_user, current_user.id):
                ot_ob_pay = calculate_overtime_pay(user_wage, ot_hours)
                month_ot_pay += ot_ob_pay

        # Handle regular rotation shifts
        from app.core.schedule import determine_shift_for_date

        result = determine_shift_for_date(current_date, start_week=current_user.id)
        if result:
            shift, rotation_week = result

            # Check for oncall override
            oncall_override = oncall_override_map.get(current_date)

            # Determine if this is effectively an OC shift (considering overrides)
            has_rotation_oc = shift and shift.code == "OC"
            is_effective_oc = (
                has_rotation_oc and not (oncall_override and oncall_override.override_type == OnCallOverrideType.REMOVE)
            ) or (oncall_override and oncall_override.override_type == OnCallOverrideType.ADD)

            if is_effective_oc:
                # Calculate on-call pay
                if can_see_salary(current_user, current_user.id):
                    oc_result = calculate_oncall_pay(current_date, user_wage, oncall_rules)
                    month_oc_pay += oc_result["total_pay"]

            elif shift and shift.code != "OFF" and shift.code != "OC":
                # Regular work shift (not OC, not OFF)
                hours, start_dt, end_dt = calculate_shift_hours(current_date, shift)
                month_total_hours += hours

                if start_dt and end_dt:
                    ob_hours_dict = calculate_ob_hours(start_dt, end_dt, combined_rules)
                    month_ob_hours += sum(ob_hours_dict.values())

                    if can_see_salary(current_user, current_user.id):
                        ob_pay_dict = calculate_ob_pay(start_dt, end_dt, combined_rules, user_wage)
                        month_total_pay += sum(ob_pay_dict.values())

        current_date += dt.timedelta(days=1)

    month_summary = {
        "total_hours": month_total_hours,
        "ob_hours": month_ob_hours,
        "total_pay": month_total_pay,
        "oc_pay": month_oc_pay,
        "ot_pay": month_ot_pay,
        "absence_deduction": month_absence_deduction,
        "month_name": safe_today.strftime("%B"),
    }

    # Check for upcoming vacation (within next 30 days, using safe_today for comparison)
    upcoming_vacation = None
    if current_user.vacation:
        current_year = safe_today.year
        next_year = current_year + 1

        for year_str in [str(current_year), str(next_year)]:
            if year_str in current_user.vacation:
                vacation_weeks = current_user.vacation[year_str]
                for week_num in vacation_weeks:
                    week_date = date.fromisocalendar(int(year_str), week_num, 1)
                    days_until_vacation = (week_date - safe_today).days

                    if 0 <= days_until_vacation <= 30:
                        week_end = week_date + dt.timedelta(days=6)
                        start_fmt = week_date.strftime("%b %d")
                        end_fmt = week_end.strftime("%b %d")
                        upcoming_vacation = {
                            "week": week_num,
                            "year": year_str,
                            "date_range": f"{start_fmt} - {end_fmt}",
                        }
                        break
            if upcoming_vacation:
                break

    return render_template(
        templates,
        "dashboard.html",
        request,
        {
            "next_shift": next_shift,
            "next_oncall_shift": next_oncall_shift,
            "week_summary": week_summary,
            "month_summary": month_summary,
            "upcoming_vacation": upcoming_vacation,
            "can_see_salary": can_see_salary(current_user, current_user.id),
        },
        user=current_user,
    )
