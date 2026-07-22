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
from app.core.helpers import can_see_salary, strip_salary_data
from app.core.holidays import get_holiday_dates_for_year
from app.core.logging_config import get_logger
from app.core.oncall import (
    _cached_oncall_rules as _get_oncall_rules,
)
from app.core.oncall import (
    _get_storhelg_dates_for_year,
)
from app.core.schedule import (
    _cached_special_rules,
    _select_ob_rules_for_date,
    build_calendar_grid_for_month,
    build_cowork_details,
    build_cowork_stats,
    build_handover_details,
    build_week_data,
    compute_day_ob_pay,
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
from app.core.schedule.summary import apply_year_pay_adjustments
from app.core.schedule.vacation import calculate_vacation_balance, fold_vacation_supplement_into_pay
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
from app.routes.shared import _resolve_person_param, build_position_nav, redirect_if_not_own_data, render

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

    The person_id parameter is resolved as a USER id whenever a User row with
    that id exists; only when no such user exists does the legacy rotation
    position interpretation apply.
    """
    if current_user is None:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)

    date_obj = validate_date_params(year, month, day)

    # Resolve the position held on the VIEWED date, so a future-dated change only
    # shows once its date is reached (mirrors the month/week/range views).
    target_user, rotation_position = _resolve_person_param(db, person_id, on_date=date_obj)
    user_id_for_wages = target_user.id if target_user is not None else person_id

    if redirect := redirect_if_not_own_data(
        current_user, user_id_for_wages, f"/day/{current_user.id}/{year}/{month}/{day}"
    ):
        return redirect

    nav = get_navigation_dates("day", date_obj)
    iso_year, iso_week, _ = date_obj.isocalendar()

    # Employment window for the viewed user at this position. It threads into
    # the canonical fetch below (before-start masking), drives the after-end
    # mask and the template flag; both edges render as OFF with hidden
    # coworkers (departed with or without a successor is treated the same).
    from app.core.schedule.person_history import get_current_person_for_position, get_employment_period

    emp_start = None
    emp_end = None
    if target_user is not None:
        emp_start, emp_end = get_employment_period(db, target_user.id, rotation_position)
    else:
        current_person = get_current_person_for_position(db, rotation_position)
        if current_person and current_person.get("effective_from"):
            emp_start = current_person["effective_from"]

    before_employment = False
    if emp_start and date_obj < emp_start:
        before_employment = True
    elif emp_end and date_obj > emp_end:
        before_employment = True

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

    # Resolve per-user rates for the viewed user (before the canonical fetch,
    # so user_rates_map prices overtime with any custom stored OT rate)
    from app.core.rates import get_user_rates

    _rate_user = (
        db.query(User).filter(User.id == user_id_for_wages).first()
        if user_id_for_wages != current_user.id
        else current_user
    )
    _user_rates = (
        get_user_rates(_rate_user, session=db, effective_date=date_obj) if _rate_user else get_user_rates(current_user)
    )

    # === Canonical shift resolution (issue #206) ===
    # The day's shift, original shift, hours and times come from
    # generate_period_data - the same canonical path the week, month and year
    # views use - instead of a parallel sequence of queries and override logic.
    from app.core.schedule import generate_period_data
    from app.core.schedule.period import mask_days_to_employment

    canonical_days = generate_period_data(
        date_obj,
        date_obj,
        person_id=rotation_position,
        session=db,
        user_rates_map={rotation_position: _user_rates} if _user_rates else None,
        employment_start=emp_start,
    )
    if canonical_days:
        canonical = canonical_days[0]
    else:
        # Date precedes the first rotation era; mirror determine_shift_for_date
        # returning (None, None): no shift, no hours, no pay.
        canonical = {
            "shift": None,
            "original_shift": None,
            "rotation_week": None,
            "hours": 0.0,
            "start": None,
            "end": None,
            "ob": {},
            "oncall_pay": 0.0,
            "oncall_details": {},
            "ot_pay": 0.0,
            "ot_hours": 0.0,
            "ot_details": {},
        }
    if emp_end is not None and date_obj > emp_end:
        canonical = mask_days_to_employment([canonical], date.min, emp_end)[0]
    before_employment = before_employment or bool(canonical.get("before_employment"))

    # A linked substitute's day (issue #290) is a real worked day: it must not
    # render with the before-employment treatment (hidden coworkers etc.).
    is_substitute_day = bool(canonical.get("is_substitute"))
    substitute_hourly_wage = canonical.get("substitute_hourly_wage") or 0
    if is_substitute_day:
        before_employment = False

    shift = canonical.get("shift")
    original_shift = canonical.get("original_shift")
    rotation_week = canonical.get("rotation_week")
    rotation_length = get_rotation_length_for_date(date_obj)
    hours = canonical.get("hours", 0.0) or 0.0
    start_dt = canonical.get("start")
    end_dt = canonical.get("end")

    if rotation_week is None and shift is not None:
        # The canonical vacation branch leaves rotation_week unset; the week
        # label is purely presentational, so backfill it from the rotation.
        _rot = determine_shift_for_date(date_obj, start_week=rotation_position)
        rotation_week = _rot[1] if _rot else None

    # A called-in OT day renders with the actual OT times, not the static OT
    # shift type times; rebuild the display shift from the canonical OT details.
    if shift and shift.code == "OT" and canonical.get("ot_details"):
        from app.core.models import ShiftType

        shift = ShiftType(
            code="OT",
            label=shift.label,
            start_time=canonical["ot_details"]["start_time"],
            end_time=canonical["ot_details"]["end_time"],
            color=shift.color,
        )

    # INVARIANT (issue #206): the raw rows fetched below - oncall_override,
    # shift_override, absence, day_pay_override and the OT row id - drive
    # edit-form prefill and detail rendering ONLY. Shift, hours and pay
    # resolution comes exclusively from generate_period_data above; do not
    # reintroduce shadow calculations on top of these rows. A new override
    # layer belongs in the batch fetchers and _populate_single_person_day in
    # period.py, where it reaches every view (day/week/month/year) at once.
    oncall_override = (
        db.query(OnCallOverride)
        .filter(OnCallOverride.user_id == user_id_for_wages, OnCallOverride.date == date_obj)
        .first()
    )
    shift_override = (
        db.query(ShiftOverride)
        .filter(ShiftOverride.user_id == user_id_for_wages, ShiftOverride.date == date_obj)
        .first()
    )
    absence = db.query(Absence).filter(Absence.user_id == user_id_for_wages, Absence.date == date_obj).first()

    # Whether this person has OC in the rotation (before any overrides)
    has_rotation_oc = bool(original_shift and original_shift.code == "OC")

    # Effectively an on-call day? Controls which pay table renders. The
    # canonical shift answers directly, except when an absence or a full
    # call-in OT shift replaced it; those days keep rendering the
    # (zeroed/reduced) on-call table when the underlying day was on-call, so
    # reconstruct that state from the same inputs the canonical path applies
    # (rotation + on-call override, with a manual shift override taking
    # priority over OC).
    if before_employment:
        is_effective_oc = False
    elif shift is not None and shift.code == "OC":
        is_effective_oc = True
    elif absence is not None or (shift is not None and shift.code == "OT"):
        is_effective_oc = bool(
            (
                (
                    has_rotation_oc
                    and not (oncall_override and oncall_override.override_type == OnCallOverrideType.REMOVE)
                )
                or (oncall_override and oncall_override.override_type == OnCallOverrideType.ADD)
            )
            and shift_override is None
        )
    else:
        is_effective_oc = False

    # OB hours and kronor through the same gate the month/year summary uses
    # (manual override wins; OFF/OC/OT days carry no OB). Partial-day absence
    # truncation and full-day absence zeroing are already reflected in the
    # canonical start/end/shift, so no re-truncation happens here.
    # A linked substitute's day (issue #290) is priced as hourly work: the OB
    # base is the substitute's hourly wage as a monthly equivalent and the
    # user's own rate overrides do not apply (same as the month summary).
    if canonical.get("is_substitute"):
        from app.core.schedule.wages import _MONTHLY_HOURS

        _sub_wage = canonical.get("substitute_hourly_wage") or 0
        ob_hours, ob_pay, _ = compute_day_ob_pay(canonical, combined_rules, int(_sub_wage * _MONTHLY_HOURS), None)
    else:
        ob_hours, ob_pay, _ = compute_day_ob_pay(canonical, combined_rules, monthly_salary, _user_rates["ob"])

    ob_codes = sorted(ob_hours.keys())
    weekday_name = weekday_names[date_obj.weekday()]

    midnight = datetime.combine(date_obj, time(0, 0))
    active_special_rules = _select_ob_rules_for_date(midnight, special_rules)

    # Overtime pay details come from the canonical dict (priced with the
    # user's OT rate via user_rates_map); only the raw OT row id is fetched
    # here, for the delete link in the edit form.
    ot_details = canonical.get("ot_details") or {}
    _ot_row = get_overtime_shift_for_date(db, user_id_for_wages, date_obj)
    ot_shift_id = _ot_row.id if _ot_row else None

    # On-call pay comes from the canonical dict, which already zeroes it on
    # absence days and reduces it around overtime (including OT crossing
    # midnight from the previous day).
    oncall_pay = canonical.get("oncall_pay", 0.0) or 0.0
    oncall_details = canonical.get("oncall_details") or {}

    # Manual hour overrides are already applied by the canonical path (OB via
    # compute_day_ob_pay's override branch, on-call inside
    # _populate_single_person_day); the row is fetched only to prefill the
    # override edit form.
    day_pay_override = (
        db.query(DayPayOverride)
        .filter(DayPayOverride.user_id == user_id_for_wages, DayPayOverride.date == date_obj)
        .first()
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

            # OB compensation for sick absence (80% of OB on sick-pay hours).
            # The window runs from the worked start (the canonical start, which
            # is arrived_at-truncated for a partial day; full shift start for a
            # full-day absence where the canonical start is None) to the FULL
            # scheduled shift end, matching how the month summary prices it.
            _sick_ob_start = start_dt if start_dt is not None else shift_start_dt
            if (
                _user_rates.get("sick", {}).get("ob_compensation")
                and sjuklon_hours_today > 0
                and _sick_ob_start is not None
                and shift_end_dt is not None
                and full_shift_hours > 0
            ):
                from app.core.schedule.ob import calculate_ob_pay as _calc_ob_pay_sick

                full_shift_ob = _calc_ob_pay_sick(
                    _sick_ob_start, shift_end_dt, combined_rules, monthly_salary, rate_overrides=_user_rates["ob"]
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

    # Vacation notice flag: a week-based vacation week (also on its OFF days,
    # which the canonical path leaves as OFF) or a day-level VACATION absence
    # (reusing the raw absence row fetched above).
    is_vacation_day = False
    if _rate_user:
        _vac_json = _rate_user.vacation or {}
        if str(iso_year) in _vac_json and iso_week in _vac_json[str(iso_year)]:
            is_vacation_day = True
    if not is_vacation_day:
        is_vacation_day = absence is not None and absence.absence_type == AbsenceType.VACATION

    return render(
        "day.html",
        {
            "request": request,
            "user": current_user,
            "person_id": person_id,
            "person_name": person_name,
            "person_nav": build_position_nav(db) if current_user.role == UserRole.ADMIN else None,
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
            "is_substitute": is_substitute_day,
            "substitute_hourly_wage": substitute_hourly_wage if show_salary else 0,
            "substitute_base_pay": (
                (hours * float(substitute_hourly_wage))
                if (is_substitute_day and show_salary and shift and shift.code in ("N1", "N2", "N3"))
                else 0.0
            ),
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

    The person_id parameter is resolved as a USER id whenever a User row with
    that id exists; only when no such user exists does the legacy rotation
    position interpretation apply.
    """
    safe_today = get_safe_today(rotation_start_date)
    iso_year, iso_week, _ = safe_today.isocalendar()

    year = year or iso_year
    week = week or iso_week

    # Resolve the position held during the VIEWED week (its Monday), so a
    # future-dated change only shows once its week is reached.
    monday = date.fromisocalendar(year, week, 1)
    target_user, rotation_position = _resolve_person_param(db, person_id, on_date=monday)
    if target_user is not None:
        person_name = target_user.name
    else:
        pos_user = db.query(User).filter(User.person_id == rotation_position, User.is_active == 1).first()
        person_name = pos_user.name if pos_user else None

    # Use rotation_position for schedule calculation
    # For user_id lookups, pass employment start/end so days outside the
    # viewer's own tenure show as before-employment (covers both a viewer who
    # hasn't started yet and one whose own tenure at this position has ended).
    week_employment_start = None
    week_employment_end = None
    if target_user is not None:
        from app.core.schedule.person_history import get_employment_period

        week_emp_start, week_emp_end = get_employment_period(db, target_user.id, rotation_position)
        week_employment_start = week_emp_start
        week_employment_end = week_emp_end

    # Redirect ANY viewer (self, another user, or an admin) once the ENTIRE
    # requested week falls after the resolved user's own tenure end at this
    # position - regardless of whether a successor has since taken over.
    if target_user is not None and week_employment_end is not None and monday > week_employment_end:
        return RedirectResponse(url=f"/week?year={year}&week={week}", status_code=302)

    # When the viewer held more than one position during this week - a swap or
    # succession landing mid-week - stitch each held position's masked segment so
    # the post-change days show the viewer's real shifts on their new position
    # instead of being blanked to OFF by the single-position employment mask.
    days_in_week = None
    if target_user is not None:
        from app.core.schedule.summary import stitch_user_week_days

        days_in_week = stitch_user_week_days(
            db,
            year,
            week,
            target_user.id,
            rotation_position,
            week_employment_start,
            week_employment_end,
        )
    if days_in_week is None:
        days_in_week = build_week_data(
            year,
            week,
            person_id=rotation_position,
            session=db,
            include_coworkers=True,
            employment_start=week_employment_start,
            employment_end=week_employment_end,
        )

    nav = get_navigation_dates("week", monday)

    real_today = get_today()

    storhelg_dates = _get_storhelg_dates_for_year(year)
    holiday_dates = get_holiday_dates_for_year(year)

    return render(
        "week.html",
        {
            "request": request,
            "user": current_user,
            "year": year,
            "week": week,
            "days": days_in_week,
            "person_id": person_id,
            "person_name": person_name,
            "person_nav": build_position_nav(db) if current_user and current_user.role == UserRole.ADMIN else None,
            "today": real_today,
            "storhelg_dates": storhelg_dates,
            "holiday_dates": holiday_dates,
            **nav,
        },
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

    # Resolve the position held at the VIEWED range's start, so a future-dated
    # change only shows once the range reaches it.
    target_user, rotation_position = _resolve_person_param(db, person_id, on_date=start)
    if target_user is not None:
        user_id_for_wages = target_user.id
        person_name = target_user.name
    else:
        user_id_for_wages = person_id
        person_name = None

    if redirect := redirect_if_not_own_data(current_user, user_id_for_wages, f"/range/{current_user.id}"):
        return redirect

    if person_name is None:
        if current_user is not None and current_user.rotation_person_id == rotation_position:
            person_name = current_user.name
        else:
            holder = db.query(User).filter(User.person_id == rotation_position).first()
            person_name = holder.name if holder else person_list[rotation_position - 1].name

    range_employment_start = None
    range_employment_end = None
    if target_user is not None:
        from app.core.schedule.person_history import get_employment_period

        emp_start, emp_end = get_employment_period(db, target_user.id, rotation_position)
        range_employment_start = emp_start
        range_employment_end = emp_end

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
                employment_end=range_employment_end,
            )
            for d in week_days:
                if start <= d["date"] <= end:
                    days_in_range.append(d)
        current_monday += timedelta(days=7)

    breakdown_days = None
    if can_see_salary(current_user, rotation_position):
        from app.core.schedule.summary import build_range_breakdown_days

        breakdown_days = build_range_breakdown_days(
            start,
            end,
            rotation_position,
            session=db,
            wage_user_id=user_id_for_wages,
            employment_start=range_employment_start,
            employment_end=range_employment_end,
        )

    years_in_range = {d["date"].year for d in days_in_range}
    storhelg_dates: set = set()
    holiday_dates: set = set()
    for yr in years_in_range:
        storhelg_dates |= _get_storhelg_dates_for_year(yr)
        holiday_dates |= get_holiday_dates_for_year(yr)

    return render(
        "range.html",
        {
            "request": request,
            "user": current_user,
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
            "breakdown_days": breakdown_days,
        },
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

    The person_id parameter is resolved as a USER id whenever a User row with
    that id exists; only when no such user exists does the legacy rotation
    position interpretation apply.
    """
    start_time = datetime.now()

    safe_today = get_safe_today(rotation_start_date)

    year = year or safe_today.year
    month = month or safe_today.month

    validate_date_params(year, month, None)

    # Resolve the position held during the VIEWED month, so a future-dated change
    # only shows once its month is reached.
    target_user, rotation_position = _resolve_person_param(db, person_id, on_date=date(year, month, 1))
    if target_user is not None:
        user_id_for_wages = target_user.id
        person_name = target_user.name
    else:
        user_id_for_wages = person_id
        person_name = None

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
    # For user_id lookups, pass the user's own employment start/end so dates
    # outside it show as before_employment - this covers both a viewer who
    # hasn't started yet and one whose own tenure at this position has ended
    # (with or without a successor since taking over).
    viewer_employment_start = None
    viewer_employment_end = None
    if target_user is not None:
        from app.core.schedule.person_history import get_employment_period

        emp_start, emp_end = get_employment_period(db, target_user.id, rotation_position)
        viewer_employment_start = emp_start
        viewer_employment_end = emp_end

    # Redirect ANY viewer (self, another user, or an admin) once the ENTIRE
    # requested month falls after the resolved user's own tenure end at this
    # position - regardless of whether a successor has since taken over.
    if target_user is not None and viewer_employment_end is not None:
        month_start = date(year, month, 1)
        if month_start > viewer_employment_end:
            return RedirectResponse(url=f"/month?year={year}&month={month}", status_code=302)

    calendar_data = build_calendar_grid_for_month(
        year,
        month,
        person_id=rotation_position,
        session=db,
        include_coworkers=True,
        employment_start=viewer_employment_start,
        employment_end=viewer_employment_end,
        viewer_user_id=target_user.id if target_user is not None else None,
        wage_user_id=user_id_for_wages,
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
                target_user
                if target_user is not None
                else db.query(User).filter(User.person_id == rotation_position).first()
            )
            if vac_user:
                try:
                    balance = calculate_vacation_balance(vac_user, year, db)
                    pay = balance.get("pay", {})
                    supplement_month = round(pay.get("supplement_per_day", 0) * sem_count, 0)
                    vacation_month = {
                        "days": sem_count,
                        "supplement_per_day": pay.get("supplement_per_day", 0),
                        "supplement_month": supplement_month,
                    }
                    # Fold the supplement into the headline gross/net totals, matching
                    # the year view's per-month behavior, so the two views agree and
                    # the summary actually reflects the employee's total pay.
                    if supplement_month > 0:
                        days_in_month["brutto_pay"], days_in_month["netto_pay"] = fold_vacation_supplement_into_pay(
                            days_in_month.get("brutto_pay", 0), days_in_month.get("netto_pay", 0), supplement_month
                        )
                except Exception:
                    logger.warning(
                        "Semestertillägg kunde inte beräknas för user_id=%s", user_id_for_wages, exc_info=True
                    )

    # Hide summary stats if the entire month is outside the viewer's own
    # employment window - before it starts, or after it ends (departed, with
    # or without a successor since taking over the position).
    import calendar as _cal_mod
    from datetime import date as _date

    before_employment_month = viewer_employment_start is not None and viewer_employment_start > _date(
        year, month, _cal_mod.monthrange(year, month)[1]
    )
    after_employment_month = viewer_employment_end is not None and viewer_employment_end < _date(year, month, 1)
    before_employment_month = before_employment_month or after_employment_month

    # Aggregated payslip-style breakdown for hourly wage users
    hourly_breakdown = None
    if show_salary:
        from app.database.database import WageType

        wage_user = (
            target_user
            if target_user is not None
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

    return render(
        "month.html",
        {
            "request": request,
            "user": current_user,
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

    safe_today = get_safe_today(rotation_start_date)
    year = year or safe_today.year
    month = month or safe_today.month

    validate_date_params(year, month, None)

    # Resolve the position held during the EXPORTED month, so a future-dated
    # change only shows once its month is reached (same as show_month_for_person).
    target_user, rotation_position = _resolve_person_param(db, person_id, on_date=date(year, month, 1))
    if target_user is not None:
        user_id_for_wages = target_user.id
        person_name = target_user.name
    else:
        user_id_for_wages = person_id
        person_name = None

    if redirect := redirect_if_not_own_data(
        current_user, user_id_for_wages, f"/month/{current_user.id}?year={year}&month={month}"
    ):
        return redirect

    if not can_see_salary(current_user, rotation_position):
        raise HTTPException(status_code=403, detail="Åtkomst nekad")

    # When resolved as a user, mask days outside their own employment window
    # (before it started, or after it ended - with or without a successor
    # since taking over the position) so the export never contains another
    # holder's real hours/pay under this user's file.
    if target_user is not None:
        from datetime import date as _date

        from app.core.rates import get_user_rates
        from app.core.schedule import generate_month_data
        from app.core.schedule.period import mask_days_to_employment
        from app.core.schedule.person_history import get_employment_period

        emp_start, emp_end = get_employment_period(db, target_user.id, rotation_position)

        # Resolve rates for the actual USER whose month this is, not whoever
        # happens to hold the rotation position, exactly as
        # build_calendar_grid_for_month does for the month view - without
        # this, per-day OT pay silently falls back to the generic formula
        # instead of a custom stored OT rate.
        _month_rates_map = None
        _rate_user = target_user
        if _rate_user is not None:
            _month_rates = get_user_rates(_rate_user, session=db, effective_date=date(year, month, 1))
            if _month_rates:
                _month_rates_map = {rotation_position: _month_rates}

        # employment_start threading + keep_substitute_days: the export must
        # include a linked substitute's pre-employment days (issue #290), same
        # as build_calendar_grid_for_month.
        month_days = generate_month_data(
            year,
            month,
            rotation_position,
            session=db,
            user_rates_map=_month_rates_map,
            employment_start=emp_start,
        )
        month_days = mask_days_to_employment(
            month_days, emp_start or _date.min, emp_end or _date.max, keep_substitute_days=True
        )
        days_in_month = summarize_month_for_person(
            year,
            month,
            rotation_position,
            session=db,
            year_days=month_days,
            payment_year=year,
            wage_user_id=user_id_for_wages,
        )
    else:
        days_in_month = summarize_month_for_person(
            year, month, rotation_position, session=db, payment_year=year, wage_user_id=user_id_for_wages
        )

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

    The person_id parameter is resolved as a USER id whenever a User row with
    that id exists; only when no such user exists does the legacy rotation
    position interpretation apply. When resolved as a user, the rotation
    position comes from PersonHistory but the user id drives wage lookups and
    employment filtering.
    """
    start_time = datetime.now()

    if current_user is None:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)

    target_user, rotation_position = _resolve_person_param(db, person_id)
    if target_user is not None:
        user_id_for_wages = target_user.id  # Use for wage lookup
        person_name = target_user.name
    else:
        user_id_for_wages = person_id  # Same as person_id for legacy positions
        person_name = None  # Will be looked up below

    safe_today = get_safe_today(rotation_start_date)
    year = year or safe_today.year

    if redirect := redirect_if_not_own_data(current_user, user_id_for_wages, f"/year/{current_user.id}?year={year}"):
        return redirect

    if with_person_id is not None:
        with_person_id = validate_person_id(with_person_id)

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

    # Use rotation_position for schedule-related calculations. Scope the cowork
    # stats to the viewed user's own employment window so a successor's days at
    # the same position are not attributed to a departed holder.
    employment_user_id = target_user.id if target_user is not None else None
    cowork_rows = build_cowork_stats(year, rotation_position, session=db, employment_user_id=employment_user_id)
    selected_other_id = None
    selected_other_name = None
    cowork_details: list[dict] = []

    if with_person_id:
        selected_other_id = with_person_id
        _other_row = next((r for r in cowork_rows if r["other_id"] == with_person_id), None)
        selected_other_name = _other_row["other_name"] if _other_row else str(with_person_id)
        cowork_details = build_cowork_details(
            year, rotation_position, with_person_id, session=db, employment_user_id=employment_user_id
        )

    # Use rotation_position for schedule, user_id_for_wages for wage lookup.
    # For user-scoped views (a User resolved) filter months to the viewed user's
    # employment period, so an admin does not see the predecessor's months.
    year_data = summarize_year_for_person(
        year,
        rotation_position,
        session=db,
        current_user=current_user,
        wage_user_id=user_id_for_wages,
        employment_user_id=target_user.id if target_user is not None else None,
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

    # Fold the vacation supplement and any employment transition into the pay
    # figures. Shared with /statistics/<id> so both pages show the same money.
    vacation_pay = None
    if show_salary:
        vac_user = (
            target_user
            if target_user is not None
            else db.query(User).filter(User.person_id == rotation_position).first()
        )
        if vac_user:
            vacation_pay = apply_year_pay_adjustments(months, year_summary, vac_user, year, db)

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

    return render(
        "year.html",
        {
            "request": request,
            "user": current_user,
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

    target_user, rotation_position = _resolve_person_param(db, person_id)
    if target_user is not None:
        user_id_for_wages = target_user.id
        person_name = target_user.name
    else:
        user_id_for_wages = person_id
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

    # Scope the cowork stats to the viewed user's own employment window so a
    # successor's days at the same position are not attributed to a departed
    # holder.
    employment_user_id = target_user.id if target_user is not None else None
    cowork_rows = build_cowork_stats(year, rotation_position, session=db, employment_user_id=employment_user_id)

    selected_other_id = None
    selected_other_name = None
    selected_cowork_row = None
    cowork_details: list[dict] = []
    handover_details: list[dict] = []

    if with_person_id:
        selected_other_id = with_person_id
        selected_cowork_row = next((r for r in cowork_rows if r["other_id"] == with_person_id), None)
        selected_other_name = selected_cowork_row["other_name"] if selected_cowork_row else str(with_person_id)
        cowork_details = build_cowork_details(
            year, rotation_position, with_person_id, session=db, employment_user_id=employment_user_id
        )
        handover_details = build_handover_details(
            year, rotation_position, with_person_id, session=db, employment_user_id=employment_user_id
        )

    return render(
        "cowork.html",
        {
            "request": request,
            "user": current_user,
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
    )
