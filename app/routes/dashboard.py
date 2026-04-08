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
from app.core.rates import get_user_rates
from app.core.schedule import (
    _cached_special_rules,
    build_week_data,
    calculate_ob_hours,
    calculate_ob_pay,
    calculate_overtime_pay,
    calculate_shift_hours,
    determine_shift_for_date,
    get_overtime_shifts_for_month,
    get_user_wage,
    ob_rules,
    rotation_start_date,
)
from app.core.schedule.wages import (
    KARENS_HOURS,
    calculate_absence_deduction,
    get_absent_hours_from_left_at,
    get_karens_consumed_before_date,
    get_shift_times_for_date,
)
from app.core.storage import calculate_tax_from_table
from app.core.utils import get_safe_today, get_today
from app.database.database import Absence, OnCallOverride, OnCallOverrideType, ShiftSwap, SwapStatus, User, get_db
from app.routes.shared import templates

router = APIRouter(tags=["dashboard"])


def _query_absence_and_deduction(
    db: Session,
    user_id: int,
    check_date: date,
    user_wage: float,
    show_salary: bool,
) -> tuple[Absence | None, float]:
    """Query absence for a date and compute the wage deduction when salary is visible."""
    absence = db.query(Absence).filter(Absence.user_id == user_id, Absence.date == check_date).first()
    deduction = 0.0
    if absence and show_salary:
        shift_hours, _, shift_end_dt = get_shift_times_for_date(db, user_id, check_date)
        absent_hours = get_absent_hours_from_left_at(absence.left_at, shift_end_dt, shift_hours)
        if absence.absence_type.value == "SICK":
            karens_consumed = get_karens_consumed_before_date(db, user_id, check_date)
            karens_remaining = max(0.0, KARENS_HOURS - karens_consumed)
            deduction = calculate_absence_deduction(
                user_wage,
                absence.absence_type.value,
                shift_hours,
                absent_hours=absent_hours,
                karens_remaining=karens_remaining,
            )
        else:
            deduction = calculate_absence_deduction(
                user_wage, absence.absence_type.value, shift_hours, absent_hours=absent_hours
            )
    return absence, deduction


def _compute_month_summary(
    year: int,
    month: int,
    person_id: int,
    user: User,
    show_salary: bool,
    db: Session,
) -> dict:
    """
    Compute month summary using the same logic as the dashboard loop.
    Rates, wage and tax table are resolved with effective_date = first day of the
    month so historical overrides are honoured correctly.
    Used for both the displayed month and the trend (previous month).
    """
    month_start = date(year, month, 1)
    month_end = date(year, month + 1, 1) - dt.timedelta(days=1) if month < 12 else date(year, 12, 31)

    # Resolve wage and rates for the specific month (date-aware)
    user_wage = get_user_wage(db, user.id, effective_date=month_start)
    user_rates = get_user_rates(user, session=db, effective_date=month_start)
    user_id = user.id
    tax_table = user.tax_table

    combined_rules = ob_rules + _cached_special_rules(year)
    oncall_rules = _cached_oncall_rules(year)

    ot_shifts = get_overtime_shifts_for_month(db, user_id, year, month)
    ot_map = {s.date: s for s in ot_shifts}

    oncall_overrides = (
        db.query(OnCallOverride)
        .filter(OnCallOverride.user_id == user_id, OnCallOverride.date >= month_start, OnCallOverride.date <= month_end)
        .all()
    )
    override_map = {o.date: o for o in oncall_overrides}

    total_hours = 0.0
    ob_hours = 0.0
    total_ob_pay = 0.0
    oc_pay = 0.0
    ot_pay = 0.0
    absence_deduction = 0.0

    current_date = month_start
    while current_date <= month_end:
        absence, deduction = _query_absence_and_deduction(db, user_id, current_date, user_wage, show_salary)
        absence_deduction += deduction

        ot_shift = ot_map.get(current_date)
        if ot_shift and not absence:
            ot_start = datetime.combine(current_date, ot_shift.start_time)
            ot_end = datetime.combine(current_date, ot_shift.end_time)
            if ot_shift.end_time < ot_shift.start_time:
                ot_end += dt.timedelta(days=1)
            ot_hours_val = (ot_end - ot_start).total_seconds() / 3600.0
            total_hours += ot_hours_val
            if show_salary:
                ot_pay += calculate_overtime_pay(user_wage, ot_hours_val, ot_hourly_rate=user_rates["ot"])

        result = determine_shift_for_date(current_date, start_week=person_id)
        if result:
            shift, _ = result
            override = override_map.get(current_date)
            has_rot_oc = shift and shift.code == "OC"
            is_effective_oc = (
                has_rot_oc and not (override and override.override_type == OnCallOverrideType.REMOVE)
            ) or (override and override.override_type == OnCallOverrideType.ADD)

            if is_effective_oc:
                if show_salary:
                    excluded = []
                    if ot_shift and not absence:
                        excluded = [
                            (
                                datetime.combine(current_date, ot_shift.start_time),
                                datetime.combine(current_date, ot_shift.end_time)
                                + (
                                    dt.timedelta(days=1) if ot_shift.end_time < ot_shift.start_time else dt.timedelta(0)
                                ),
                            )
                        ]
                    oc_result = calculate_oncall_pay(
                        current_date,
                        user_wage,
                        oncall_rules,
                        rate_overrides=user_rates["oncall"],
                        excluded_intervals=excluded or None,
                    )
                    oc_pay += oc_result["total_pay"]
            elif shift and shift.code != "OFF" and shift.code != "OC":
                hours, start_dt, end_dt = calculate_shift_hours(current_date, shift)
                total_hours += hours
                if start_dt and end_dt:
                    ob_h = calculate_ob_hours(start_dt, end_dt, combined_rules)
                    ob_hours += sum(ob_h.values())
                    if show_salary:
                        ob_p = calculate_ob_pay(
                            start_dt, end_dt, combined_rules, user_wage, rate_overrides=user_rates["ob"]
                        )
                        total_ob_pay += sum(ob_p.values())

        current_date += dt.timedelta(days=1)

    gross_pay = total_ob_pay + oc_pay + ot_pay + user_wage - absence_deduction

    taxes = 0.0
    if tax_table:
        try:
            payment_year = year + 1 if month == 12 else year
            taxes = calculate_tax_from_table(gross_pay, tax_table, year=payment_year)
        except Exception:
            pass

    net_pay = gross_pay - taxes
    ob_pct = (ob_hours / total_hours * 100) if total_hours > 0 else 0.0

    return {
        "total_hours": total_hours,
        "ob_hours": ob_hours,
        "ob_percentage": ob_pct,
        "total_ob_pay": total_ob_pay,
        "oc_pay": oc_pay,
        "ot_pay": ot_pay,
        "absence_deduction": absence_deduction,
        "month_number": month,
        "year": year,
        "gross_pay": gross_pay,
        "taxes": taxes,
        "net_pay": net_pay,
    }


@router.get("/", response_class=HTMLResponse)
async def read_root(
    request: Request,
    month: int | None = None,
    year: int | None = None,
    week: int | None = None,
    wyear: int | None = None,
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

    # Determine which week and month to display (query params override current date)
    view_iso_year_w = wyear if wyear else iso_year
    view_iso_week = week if week and 1 <= week <= 53 else iso_week
    view_month = month if month and 1 <= month <= 12 else safe_today.month
    view_year = year if year else safe_today.year

    is_current_week = view_iso_week == iso_week and view_iso_year_w == iso_year
    is_current_month = view_month == safe_today.month and view_year == safe_today.year

    # Compute prev/next week URLs
    view_week_monday = date.fromisocalendar(view_iso_year_w, view_iso_week, 1)
    prev_week_monday = view_week_monday - timedelta(days=7)
    next_week_monday = view_week_monday + timedelta(days=7)
    pw_y, pw_w, _ = prev_week_monday.isocalendar()
    nw_y, nw_w, _ = next_week_monday.isocalendar()
    prev_week_url = f"/?week={pw_w}&wyear={pw_y}"
    next_week_url = f"/?week={nw_w}&wyear={nw_y}"

    # Compute prev/next month URLs
    prev_m = view_month - 1 if view_month > 1 else 12
    prev_m_y = view_year if view_month > 1 else view_year - 1
    next_m = view_month + 1 if view_month < 12 else 1
    next_m_y = view_year if view_month < 12 else view_year + 1
    prev_month_url = f"/?month={prev_m}&year={prev_m_y}"
    next_month_url = f"/?month={next_m}&year={next_m_y}"

    # Get user's rotation position (person_id field, or fallback to user.id)
    person_id = current_user.rotation_person_id

    # Build the viewed week's data for the user (pass session for oncall override support)
    week_data = build_week_data(view_iso_year_w, view_iso_week, person_id=person_id, session=db)

    # Create compact week schedule for dashboard display
    week_schedule = []
    for day in week_data:
        shift = day.get("shift")
        week_schedule.append(
            {
                "date": day["date"],
                "weekday": day["weekday_name"][:3],  # Mon, Tue, etc.
                "code": shift.code if shift else "?",
                "color": shift.color if shift else None,
                "is_today": day["date"] == safe_today,
                "is_past": day["date"] < safe_today,
            }
        )

    # Batch fetch overtime shifts for current and next month to avoid N+1 queries
    current_month = safe_today.month
    current_year_num = safe_today.year
    next_month = current_month + 1 if current_month < 12 else 1
    next_month_year = current_year_num if current_month < 12 else current_year_num + 1

    ot_shifts_current = get_overtime_shifts_for_month(db, current_user.id, current_year_num, current_month)
    ot_shifts_next = get_overtime_shifts_for_month(db, current_user.id, next_month_year, next_month)

    # Create lookup dictionary for O(1) access (used for upcoming-shift detection)
    ot_shift_map = {shift.date: shift for shift in ot_shifts_current + ot_shifts_next}

    # OT map for the viewed week (may differ from current month)
    view_week_months: set[tuple[int, int]] = set()
    for _d in range(7):
        _day = view_week_monday + dt.timedelta(days=_d)
        view_week_months.add((_day.year, _day.month))
    _view_week_ot: list = []
    for _wy, _wm in view_week_months:
        _view_week_ot.extend(get_overtime_shifts_for_month(db, current_user.id, _wy, _wm))
    view_week_ot_map = {s.date: s for s in _view_week_ot}

    # On-call overrides for the viewed week (needed to detect OC+OT same day)
    view_week_sunday = view_week_monday + timedelta(days=6)
    week_oncall_overrides = (
        db.query(OnCallOverride)
        .filter(
            OnCallOverride.user_id == current_user.id,
            OnCallOverride.date >= view_week_monday,
            OnCallOverride.date <= view_week_sunday,
        )
        .all()
    )
    week_oncall_override_map = {o.date: o for o in week_oncall_overrides}

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

        check_week_data = build_week_data(check_year, check_week, person_id=person_id, session=db)

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
                    "shift_code": "OT",
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
                    "shift_code": shift.code,
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
                    "shift_code": shift.code,
                    "color": shift.color or "#666",
                    "time_range": time_range,
                    "days_until": days_until,
                }

    # Setup OB/oncall rules for the viewed week
    combined_rules_w = ob_rules + _cached_special_rules(view_iso_year_w)
    oncall_rules_w = _cached_oncall_rules(view_iso_year_w)
    user_wage = get_user_wage(db, current_user.id)
    _user_rates = get_user_rates(current_user, session=db)
    show_salary = can_see_salary(current_user, current_user.id)

    # Calculate week summary
    week_summary = None
    if week_data:
        total_hours = 0.0
        ob_hours = 0.0
        total_pay = 0.0
        oc_pay = 0.0
        ot_pay = 0.0
        absence_deduction = 0.0

        for day in week_data:
            # Check for absence first
            absence, deduction = _query_absence_and_deduction(db, current_user.id, day["date"], user_wage, show_salary)
            absence_deduction += deduction

            # Check for overtime shift first (use dictionary lookup)
            ot_shift = view_week_ot_map.get(day["date"])

            if ot_shift and not absence:  # Skip OT if there's an absence
                # Calculate OT hours and pay
                ot_start = datetime.combine(day["date"], ot_shift.start_time)
                ot_end = datetime.combine(day["date"], ot_shift.end_time)

                # Handle shifts that cross midnight
                if ot_shift.end_time < ot_shift.start_time:
                    ot_end += dt.timedelta(days=1)

                ot_hours = (ot_end - ot_start).total_seconds() / 3600.0
                total_hours += ot_hours

                if show_salary:
                    ot_ob_pay = calculate_overtime_pay(user_wage, ot_hours, ot_hourly_rate=_user_rates["ot"])
                    ot_pay += ot_ob_pay

            # Determine effective on-call status using rotation shift + override
            # (day["shift"] may show OT which masks an OC override on the same day)
            _week_override = week_oncall_override_map.get(day["date"])
            _rot_result = determine_shift_for_date(day["date"], start_week=person_id)
            _rot_shift = _rot_result[0] if _rot_result else None
            _has_rot_oc = _rot_shift and _rot_shift.code == "OC"
            _is_effective_oc = (
                _has_rot_oc and not (_week_override and _week_override.override_type == OnCallOverrideType.REMOVE)
            ) or (_week_override and _week_override.override_type == OnCallOverrideType.ADD)

            if _is_effective_oc and show_salary:
                _oc_excluded = []
                _ot_on_oc_day = view_week_ot_map.get(day["date"])
                if _ot_on_oc_day:
                    _ot_s = datetime.combine(day["date"], _ot_on_oc_day.start_time)
                    _ot_e = datetime.combine(day["date"], _ot_on_oc_day.end_time)
                    if _ot_on_oc_day.end_time < _ot_on_oc_day.start_time:
                        _ot_e += dt.timedelta(days=1)
                    _oc_excluded = [(_ot_s, _ot_e)]
                oc_result = calculate_oncall_pay(
                    day["date"],
                    user_wage,
                    oncall_rules_w,
                    rate_overrides=_user_rates["oncall"],
                    excluded_intervals=_oc_excluded or None,
                )
                oc_pay += oc_result["total_pay"]

            # Handle regular rotation shifts (only if day is not purely on-call with no OT)
            if day["shift"] and not (_is_effective_oc and day["shift"].code == "OC"):
                if day["shift"].code not in ("OC", "OFF") and day["shift"].start_time is not None:
                    # Calculate regular shift hours
                    hours, start_dt, end_dt = calculate_shift_hours(day["date"], day["shift"])
                    total_hours += hours

                    # Calculate OB hours and pay if we have valid datetimes
                    if start_dt and end_dt:
                        ob_hours_dict = calculate_ob_hours(start_dt, end_dt, combined_rules_w)
                        ob_hours += sum(ob_hours_dict.values())

                        if show_salary:
                            ob_pay_dict = calculate_ob_pay(
                                start_dt, end_dt, combined_rules_w, user_wage, rate_overrides=_user_rates["ob"]
                            )
                            total_pay += sum(ob_pay_dict.values())

        week_summary = {
            "total_hours": total_hours,
            "ob_hours": ob_hours,
            "total_pay": total_pay,
            "oc_pay": oc_pay,
            "ot_pay": ot_pay,
            "absence_deduction": absence_deduction,
        }

    # Calculate month summary for the viewed month
    month_summary = _compute_month_summary(
        view_year,
        view_month,
        person_id,
        current_user,
        show_salary,
        db,
    )
    month_summary["month_name"] = date(view_year, view_month, 1).strftime("%B")

    # Calculate trend vs last month
    trend = None
    if show_salary:
        try:
            # Get the month before the viewed month
            if view_month == 1:
                last_month_year = view_year - 1
                last_month = 12
            else:
                last_month_year = view_year
                last_month = view_month - 1

            last_month_data = _compute_month_summary(
                last_month_year,
                last_month,
                person_id,
                current_user,
                show_salary,
                db,
            )

            if last_month_data and last_month_data.get("net_pay", 0) > 0:
                diff = month_summary["net_pay"] - last_month_data["net_pay"]
                diff_percent = (diff / last_month_data["net_pay"]) * 100
                trend = {
                    "diff": diff,
                    "diff_percent": diff_percent,
                    "direction": "up" if diff > 0 else "down" if diff < 0 else "same",
                    "last_month_name": dt.date(last_month_year, last_month, 1).strftime("%B"),
                    "last_month_number": last_month,
                }
        except Exception:
            pass  # If trend calculation fails, just skip it

    month_summary["trend"] = trend

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

    # Check for pending shift swap requests
    pending_swap_count = (
        db.query(ShiftSwap)
        .filter(ShiftSwap.target_id == current_user.id, ShiftSwap.status == SwapStatus.PENDING)
        .count()
    )

    return render_template(
        templates,
        "dashboard.html",
        request,
        {
            "next_shift": next_shift,
            "next_oncall_shift": next_oncall_shift,
            "week_summary": week_summary,
            "week_schedule": week_schedule,
            "month_summary": month_summary,
            "upcoming_vacation": upcoming_vacation,
            "can_see_salary": show_salary,
            "pending_swap_count": pending_swap_count,
            "is_current_week": is_current_week,
            "is_current_month": is_current_month,
            "prev_week_url": prev_week_url,
            "next_week_url": next_week_url,
            "prev_month_url": prev_month_url,
            "next_month_url": next_month_url,
            "view_iso_week": view_iso_week,
            "view_iso_year_w": view_iso_year_w,
        },
        user=current_user,
    )
