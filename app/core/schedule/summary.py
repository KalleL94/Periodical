"""Monthly and yearly schedule summaries."""

from typing import NamedTuple

from app.core.logging_config import get_logger
from app.core.storage import load_persons, load_tax_brackets

from .core import get_settings, weekday_names
from .ob import compute_day_ob_pay, get_combined_rules_for_year
from .period import generate_month_data, generate_period_data, mask_days_to_employment
from .wages import (
    _MONTHLY_HOURS,
    get_absence_deductions_for_month,
    get_all_user_wages,
    get_effective_monthly_wage,
    get_user_wage,
)

logger = get_logger(__name__)

_tax_brackets = None
_persons = None


def _get_tax_brackets():
    global _tax_brackets
    if _tax_brackets is None:
        _tax_brackets = load_tax_brackets()
    return _tax_brackets


def _get_persons():
    global _persons
    if _persons is None:
        _persons = load_persons()
    return _persons


def _calculate_tax(brutto: float, tax_table: str | None = None, payment_year: int | None = None) -> float:
    """
    Beräknar skatt baserat på bruttolön.

    Args:
        brutto: Bruttolön i SEK
        tax_table: Skattetabellnummer (t.ex. "33"). Om None används tax_brackets.json
        payment_year: Utbetalningsår för att välja rätt skattetabell

    Returns:
        Skattebelopp i SEK
    """
    import logging

    from app.core.storage import calculate_tax_bracket, calculate_tax_from_table

    logger = logging.getLogger(__name__)

    # Use the tax table if one is configured
    if tax_table:
        try:
            return calculate_tax_from_table(brutto, tax_table, year=payment_year)
        except Exception as e:
            # Fall back to tax brackets if the table lookup fails
            logger.warning(f"Failed to calculate tax from table {tax_table}: {e}. Using fallback.")

    # Fallback to legacy tax-bracket system
    return calculate_tax_bracket(brutto, _get_tax_brackets())


def _attach_calendar_day_breakdown(days_out: list[dict]) -> None:
    """Aggregate OB/OT hours per calendar day and attach them to each day in place.

    A night shift crossing midnight contributes to both calendar days, e.g. Sat 22:00-Sun
    06:30 gives 2h OB on Saturday and 6.5h on Sunday; days that overlap are merged
    (Sunday 6.5 + 2 = 8.5h when the person also works Sun-Mon).
    """
    calendar_day_ob: dict = {}
    for day_data in days_out:
        for cal_date, ob_dict in day_data.get("ob_hours_by_day", {}).items():
            bucket = calendar_day_ob.setdefault(cal_date, {})
            for code, hrs in ob_dict.items():
                bucket[code] = bucket.get(code, 0.0) + hrs

    calendar_day_ot: dict = {}
    for day_data in days_out:
        for cal_date, ot_hrs in day_data.get("ot_hours_by_day", {}).items():
            if ot_hrs > 0:
                calendar_day_ot[cal_date] = calendar_day_ot.get(cal_date, 0.0) + ot_hrs

    for day_data in days_out:
        day_data["ob_hours_calendar_day"] = calendar_day_ob.get(day_data["date"], {})
        day_data["ot_hours_calendar_day"] = calendar_day_ot.get(day_data["date"], 0.0)


def _resolve_person_name(session, person_id: int, on_date, persons) -> str:
    """Resolve the display name for a rotation position on a date via the history system."""
    if session:
        from app.core.schedule.person_history import get_person_for_date

        person_info = get_person_for_date(session, person_id, on_date)
        if person_info:
            return person_info["name"]
    return persons[person_id - 1].name


def _apply_absence_info_to_totals(totals: dict, absence_info: dict) -> list:
    """Copy a month's absence figures into the running totals and adjust gross pay.

    Subtracts the absence deduction and adds back sick-pay OB compensation. Returns the
    per-day absence details list.
    """
    totals["absence_deduction"] = absence_info["total_deduction"]
    totals["absence_hours"] = absence_info["total_hours"]
    totals["sick_days"] = absence_info["sick_days"]
    totals["sick_hours"] = absence_info["sick_hours"]
    totals["sick_ob_pay"] = absence_info.get("sick_ob_pay", 0.0)
    totals["sick_ob_pay_by_code"] = absence_info.get("sick_ob_pay_by_code", {})
    totals["sick_ob_hours_by_code"] = absence_info.get("sick_ob_hours_by_code", {})
    totals["sick_total_ob"] = absence_info.get("sick_total_ob", 0.0)
    totals["sick_ob_lost"] = absence_info.get("sick_ob_lost", 0.0)
    totals["vab_days"] = absence_info["vab_days"]
    totals["vab_hours"] = absence_info["vab_hours"]
    totals["leave_days"] = absence_info["leave_days"]
    totals["leave_hours"] = absence_info["leave_hours"]
    totals["off_days"] = absence_info["off_days"]
    totals["off_hours"] = absence_info["off_hours"]
    totals["parental_days"] = absence_info.get("parental_days", 0)
    totals["parental_hours"] = absence_info.get("parental_hours", 0.0)

    totals["brutto_pay"] -= totals["absence_deduction"]
    totals["brutto_pay"] += totals["sick_ob_pay"]

    return absence_info["details"]


def _hourly_corrected_gross(
    current_gross: float,
    base_salary: float,
    worked_hours: float,
    absent_hours: float,
    hourly_rate: float,
) -> float:
    """Gross pay for an hourly worker: swap the theoretical monthly base for actual hours.

    absence_hours are the hours period.py zeroes out (not in worked_hours); they are added
    back so (worked + absent) x hourly_rate - absence_deduction yields the correct sick-pay base.
    """
    return current_gross - base_salary + (worked_hours + absent_hours) * hourly_rate


class _MonthWageContext(NamedTuple):
    base_salary: int
    tax_table: str | None
    user: object | None
    user_rates: dict | None


def _resolve_month_wage_context(
    session,
    uid_for_wages: int,
    settings,
    month_start_date,
    fetch_tax_table: bool,
    user_wages: dict[int, int] | None,
) -> _MonthWageContext:
    """Resolve base salary, tax table, the User row and per-user rates for the month."""
    if user_wages and uid_for_wages in user_wages:
        base_salary = get_effective_monthly_wage(
            session, uid_for_wages, settings.monthly_salary, effective_date=month_start_date
        )
    else:
        try:
            base_salary = get_effective_monthly_wage(
                session, uid_for_wages, settings.monthly_salary, effective_date=month_start_date
            )
        except Exception:
            base_salary = settings.monthly_salary

    tax_table = None
    user = None
    if session:
        from app.database.database import User

        user = session.query(User).filter(User.id == uid_for_wages).first()

    if fetch_tax_table and user:
        import logging

        logger = logging.getLogger(__name__)
        logger.info(f"Looking up tax_table for user_id={uid_for_wages}, user found: {user is not None}")
        logger.info(f"User {user.username} has tax_table: {user.tax_table}")
        if user.tax_table:
            tax_table = user.tax_table

    from app.core.rates import get_user_rates

    user_rates = get_user_rates(user, session=session, effective_date=month_start_date) if user else None

    return _MonthWageContext(base_salary, tax_table, user, user_rates)


def summarize_month_for_person(
    year: int,
    month: int,
    person_id: int,
    session=None,
    user_wages: dict[int, int] | None = None,
    year_days: list[dict] | None = None,
    fetch_tax_table: bool = True,
    payment_year: int | None = None,
    wage_user_id: int | None = None,
) -> dict:
    """
    Detaljerad månadsöversikt för en person.

    Args:
        year: År
        month: Månad (1-12)
        person_id: Rotationsposition (1-10) för schemaberäkning
        session: SQLAlchemy session
        user_wages: Förladdade löner
        year_days: Förgenererad årsdata (optimering)
        wage_user_id: Användar-ID för löneberäkning (om annan än person_id)

    Returns:
        Dict med total_hours, num_shifts, ob_hours, ob_pay, brutto/netto, days
    """
    combined_rules = get_combined_rules_for_year(year)
    settings = get_settings()
    persons = _get_persons()

    # Resolve wage for this specific month using the first day of the month
    from datetime import date as dt_date

    month_start_date = dt_date(year, month, 1)

    # Use wage_user_id for wage lookup if provided, otherwise use person_id
    # This allows Rickard (user_id=11, rotation_position=3) to see his own wage
    uid_for_wages = wage_user_id if wage_user_id is not None else person_id

    ctx = _resolve_month_wage_context(session, uid_for_wages, settings, month_start_date, fetch_tax_table, user_wages)
    base_salary = ctx.base_salary
    tax_table = ctx.tax_table
    user = ctx.user
    user_rates = ctx.user_rates
    _rates_map = {person_id: user_rates} if user_rates else None

    # Use pre-generated data if provided, otherwise generate it
    if year_days is None:
        days = generate_month_data(
            year, month, person_id, session=session, user_wages=user_wages, user_rates_map=_rates_map
        )
    else:
        days = year_days

    # Initialize totals
    totals = {
        "total_hours": 0.0,
        "num_shifts": 0,
        "ob_hours": {},
        "ob_pay": {},
        "brutto_pay": base_salary,
        "oncall_pay": 0.0,
        "oncall_hours": 0.0,
        "ot_pay": 0.0,
        "ot_hours": 0.0,
        "absence_deduction": 0.0,
        "absence_hours": 0.0,
        "sick_days": 0,
        "sick_hours": 0.0,
        "sick_ob_pay": 0.0,
        "sick_total_ob": 0.0,
        "sick_ob_lost": 0.0,
        "vab_days": 0,
        "vab_hours": 0.0,
        "leave_days": 0,
        "leave_hours": 0.0,
        "parental_days": 0,
        "parental_hours": 0.0,
        "vacation_days": 0,
        "substitute_hours": 0.0,
        "substitute_base_pay": 0.0,
    }

    days_out = []
    week_parental_days = 0

    for day in days:
        # Filter to the correct year and month (important when all_work_days spans multiple years)
        if day["date"].year != year or day["date"].month != month:
            continue

        shift = day.get("shift")
        if shift is not None and getattr(shift, "code", None) == "SEM":
            totals["vacation_days"] += 1
        # Week-based parental leave renders as LEAVE but is flagged; count it separately
        # so it is not lost (day-level parental is counted via absence records below).
        if day.get("parental_leave"):
            week_parental_days += 1

        day_data = _process_day_for_summary(
            day,
            combined_rules,
            base_salary,
            totals,
            ob_rate_overrides=user_rates.get("ob") if user_rates else None,
        )
        # Carry the parental-leave marker through so exports can label the day correctly.
        if day.get("parental_leave"):
            day_data["parental_leave"] = True
        days_out.append(day_data)

    # Aggregate OB/OT hours per calendar day for the per-day breakdown toggle.
    _attach_calendar_day_breakdown(days_out)

    # Fetch absence deductions for the month. Absences are stored per USER id
    # (Absence.user_id), so key the lookup on uid_for_wages: identical to
    # person_id for legacy users, and the viewed user's own absences when
    # wage_user_id is set (a rotation position must not read another user's rows).
    absence_details = []
    if session:
        absence_info = get_absence_deductions_for_month(
            session,
            uid_for_wages,
            year,
            month,
            base_salary,
            ob_rules=combined_rules,
            ob_rate_overrides=user_rates.get("ob") if user_rates else None,
        )
        absence_details = _apply_absence_info_to_totals(totals, absence_info)

        # For hourly-wage users: replace the theoretical monthly base (hourly_rate×173.33)
        # with actual scheduled hours × hourly rate so gross matches the pay-slip table.
        from app.database.database import WageType

        if user and user.wage_type == WageType.HOURLY:
            actual_hourly_rate = float(
                get_user_wage(session, uid_for_wages, settings.monthly_salary, effective_date=month_start_date)
            )
            # Substitute hours are excluded: they are priced separately with the
            # substitute's own hourly wage (issue #290), not the user's rate.
            worked_hours = totals["total_hours"] - totals["ot_hours"] - totals.get("substitute_hours", 0.0)
            totals["brutto_pay"] = _hourly_corrected_gross(
                totals["brutto_pay"], base_salary, worked_hours, totals.get("absence_hours", 0.0), actual_hourly_rate
            )

    # Add week-based parental leave (flagged days) on top of day-level parental absences.
    totals["parental_days"] += week_parental_days

    # Calculate net pay using the user's tax table for the payment year
    netto_pay = totals["brutto_pay"] - _calculate_tax(totals["brutto_pay"], tax_table, payment_year=payment_year)

    person_name = _resolve_person_name(session, person_id, month_start_date, persons)

    return {
        "year": year,
        "month": month,
        "person_id": person_id,
        "person_name": person_name,
        "total_hours": totals["total_hours"],
        "num_shifts": totals["num_shifts"],
        "ob_hours": totals["ob_hours"],
        "ob_pay": totals["ob_pay"],
        "oncall_pay": totals["oncall_pay"],
        "oncall_hours": totals["oncall_hours"],
        "ot_pay": totals["ot_pay"],
        "ot_hours": totals.get("ot_hours", 0.0),
        "absence_deduction": totals["absence_deduction"],
        "absence_hours": totals["absence_hours"],
        "sick_days": totals["sick_days"],
        "sick_hours": totals.get("sick_hours", 0.0),
        "sick_ob_pay": totals.get("sick_ob_pay", 0.0),
        "sick_ob_pay_by_code": totals.get("sick_ob_pay_by_code", {}),
        "sick_ob_hours_by_code": totals.get("sick_ob_hours_by_code", {}),
        "sick_total_ob": totals.get("sick_total_ob", 0.0),
        "sick_ob_lost": totals.get("sick_ob_lost", 0.0),
        "vab_days": totals["vab_days"],
        "vab_hours": totals.get("vab_hours", 0.0),
        "leave_days": totals["leave_days"],
        "leave_hours": totals.get("leave_hours", 0.0),
        "off_days": totals.get("off_days", 0),
        "off_hours": totals.get("off_hours", 0.0),
        "parental_days": totals.get("parental_days", 0),
        "parental_hours": totals.get("parental_hours", 0.0),
        "vacation_days": totals.get("vacation_days", 0),
        "substitute_hours": totals.get("substitute_hours", 0.0),
        "substitute_base_pay": totals.get("substitute_base_pay", 0.0),
        "absence_details": absence_details,
        "brutto_pay": totals["brutto_pay"],
        "netto_pay": netto_pay,
        "base_salary": base_salary,
        "wage_type": user.wage_type if user else None,
        "tax_table": tax_table,
        "days": days_out,
    }


def build_calendar_grid_for_month(
    year: int,
    month: int,
    person_id: int,
    session=None,
    user_wages: dict[int, int] | None = None,
    include_coworkers: bool = False,
    employment_start=None,
    employment_end=None,
    viewer_user_id: int | None = None,
    wage_user_id: int | None = None,
) -> dict:
    """
    Bygger en komplett kalendergrid inklusive intilliggande månaders dagar.

    Args:
        year: År
        month: Månad (1-12)
        person_id: Person-ID
        session: SQLAlchemy session
        user_wages: Förladdade löner
        employment_start: Mask days before this date to OFF (viewer not yet employed)
        employment_end: Mask days after this date to OFF. Used when the viewer's
            own employment for this position has ended (with or without a
            successor since taking over) - the grid must render the viewer's
            own OFF days, not a successor's real schedule.
        viewer_user_id: The user whose page this is. When set, the adjacent-month
            padding days that complete the calendar's partial weeks are
            re-resolved against this user's own PersonHistory: if the viewer held
            a DIFFERENT position on a padding date (e.g. a position swap on the
            month boundary), that day is re-fetched under the correct position
            instead of being masked to OFF by the main month's employment window.
            Padding dates outside the viewer's own tenure entirely stay OFF.
        wage_user_id: The USER id whose rates (RateHistory) should price this
            month's OT/on-call pay, when it differs from person_id (a rotation
            position may be held by different users over time). Falls back to
            person_id when not set, matching summarize_month_for_person and
            summarize_year_for_person.

    Returns:
        Dict med 'summary' (månadssammanfattning) och 'grid' (lista med veckor)
    """
    import calendar as cal
    from datetime import date as dt_date
    from datetime import timedelta

    from app.core.rates import get_user_rates
    from app.database.database import User

    # Resolve the rates for the actual USER whose month this is (not just
    # whoever currently holds the rotation position), effective at the start
    # of the viewed month. This must be threaded into generate_month_data /
    # generate_period_data below as user_rates_map, keyed by person_id: without
    # it, per-day OT pay silently falls back to the generic
    # monthly-wage/OT_RATE_DIVISOR formula instead of a custom stored OT rate
    # (OB pay does not have this problem - it is recomputed fresh from
    # user_rates inside summarize_month_for_person regardless of this map).
    _rate_uid = wage_user_id if wage_user_id is not None else person_id
    _month_rates_map = None
    _month_rates = None
    if session is not None and _rate_uid is not None:
        _rate_user = session.query(User).filter(User.id == _rate_uid).first()
        if _rate_user is not None:
            _month_rates = get_user_rates(_rate_user, session=session, effective_date=dt_date(year, month, 1))
            if _month_rates:
                _month_rates_map = {person_id: _month_rates}

    # When the viewer (a real user with PersonHistory) held more than one
    # position during this month - a swap or succession landing mid-month - the
    # single-position fetch+mask below would blank every day after the change to
    # OFF, hiding the shifts the viewer actually works on their new position.
    # Detect that case and build the month from the stitched per-segment days
    # instead, so the summary totals (and the in-month grid days re-resolved
    # further below) reflect the viewer's real schedule on both sides of the
    # change. Mirrors the year view's user-scoped path.
    stitched_month_days = None
    if session is not None and viewer_user_id is not None:
        from app.core.schedule.person_history import get_user_history

        _emp_records = sorted(get_user_history(session, viewer_user_id), key=lambda r: r["effective_from"])
        if _emp_records:
            _m_first = dt_date(year, month, 1)
            _m_last = dt_date(year, month, cal.monthrange(year, month)[1])
            _month_segments = _records_overlapping_work_month(_emp_records, _m_first, _m_last)
            if len(_month_segments) > 1:
                stitched_month_days = _stitch_user_month_days(
                    session, year, month, _month_segments, user_wages, _month_rates
                )

    # Get the monthly summary for totals and day data. When the viewer's own
    # employment window is bounded (either end), generate the exact-month days
    # once and mask them to that window before summarizing, so the aggregate
    # totals (and anything the route derives from days_in_month, e.g. the
    # hourly payslip breakdown) agree with the masked calendar grid below -
    # neither a predecessor's nor a successor's real hours leak into a viewer's
    # page for months outside their own tenure.
    if stitched_month_days is not None:
        month_summary = summarize_month_for_person(
            year,
            month,
            person_id,
            session,
            user_wages,
            year_days=stitched_month_days,
            payment_year=year,
            wage_user_id=wage_user_id,
        )
    elif employment_start is not None or employment_end is not None:
        # employment_start is threaded so the before-employment branch can inject
        # linked-substitute days (issue #290); the mask keeps them for the summary
        # (they belong to the viewed user) while still zeroing everything else
        # outside the employment window.
        month_days = generate_month_data(
            year,
            month,
            person_id,
            session=session,
            user_wages=user_wages,
            user_rates_map=_month_rates_map,
            employment_start=employment_start,
        )
        month_days = mask_days_to_employment(
            month_days, employment_start or dt_date.min, employment_end or dt_date.max, keep_substitute_days=True
        )
        month_summary = summarize_month_for_person(
            year,
            month,
            person_id,
            session,
            user_wages,
            year_days=month_days,
            payment_year=year,
            wage_user_id=wage_user_id,
        )
    else:
        month_summary = summarize_month_for_person(
            year, month, person_id, session, user_wages, payment_year=year, wage_user_id=wage_user_id
        )

    # Determine grid boundaries from the weekday of the first and last day
    first_day = dt_date(year, month, 1)
    first_weekday = first_day.weekday()  # 0=Monday, 6=Sunday

    last_day_num = cal.monthrange(year, month)[1]
    last_day = dt_date(year, month, last_day_num)
    last_weekday = last_day.weekday()

    # Expand range to include full weeks at both ends
    grid_start = first_day - timedelta(days=first_weekday)
    grid_end = last_day + timedelta(days=(6 - last_weekday))

    # Generate schedule data for the extended range
    extended_days = generate_period_data(
        grid_start,
        grid_end,
        person_id,
        session=session,
        user_wages=user_wages,
        employment_start=employment_start,
        user_rates_map=_month_rates_map,
    )
    if employment_end is not None:
        extended_days = mask_days_to_employment(extended_days, dt_date.min, employment_end)

    # Fetch all persons' data if coworkers requested
    all_persons_data = None
    if include_coworkers and person_id is not None:
        all_persons_extended = generate_period_data(
            grid_start, grid_end, person_id=None, session=session, user_wages=user_wages, include_substitutes=True
        )
        # Build lookup: date -> persons list
        all_persons_data = {day["date"]: day.get("persons", []) for day in all_persons_extended}

    def _attach_coworkers(day_data: dict, source_day: dict, holder_position: int) -> None:
        """Compute and attach the coworker list for one rendered day.

        holder_position is the rotation position whose schedule source_day
        represents; it may differ from the main grid's person_id when a padding
        day was re-resolved to another of the viewer's own positions.
        """
        if not (include_coworkers and holder_position is not None and all_persons_data):
            return
        from .cowork import get_coworkers_for_day

        actual_shift = source_day.get("shift")

        # For OT shifts with time-based matching, use a special marker
        # For regular shifts, use original_shift if available, otherwise actual shift
        if actual_shift and actual_shift.code == "OT":
            # Use OT as shift_code to trigger time-based matching
            original_shift = source_day.get("original_shift")
            # If original_shift is a work shift, use it; otherwise use "OT" for time matching
            if original_shift and original_shift.code in ("N1", "N2", "N3"):
                shift_code = original_shift.code
            else:
                shift_code = "OT"  # Will use time-based matching
        else:
            # Use actual_shift directly - if this person has a swap, actual_shift
            # already reflects the swapped shift code.
            shift_code = actual_shift.code if actual_shift else "OFF"

        persons_today = all_persons_data.get(day_data["date"], [])
        coworkers = get_coworkers_for_day(
            holder_position, shift_code, persons_today, source_day.get("start"), source_day.get("end")
        )
        day_data["coworkers"] = coworkers

    # Build date lookup with is_current_month flag
    days_by_date = {}
    for day in extended_days:
        day_date = day["date"]
        is_current_month = day_date.month == month and day_date.year == year
        day_data = {
            "date": day_date,
            "shift": day.get("shift"),
            "rotation_week": day.get("rotation_week"),
            "rotation_length": day.get("rotation_length"),
            "hours": day.get("hours", 0.0),
            "start": day.get("start"),
            "end": day.get("end"),
            "weekday_name": day.get("weekday_name"),
            "is_current_month": is_current_month,
            "partial_absence": day.get("partial_absence"),
            "is_substitute": day.get("is_substitute"),
        }

        _attach_coworkers(day_data, day, person_id)

        days_by_date[day_date] = day_data

    # Correct any grid day - adjacent-month padding OR a day of the viewed month
    # itself - that the viewer actually worked on a DIFFERENT position than the
    # single one the extended range was fetched under. This covers a position
    # change on the month boundary (padding days in the other position, e.g. a
    # swap on Sep 30 / Oct 1) as well as one landing mid-month (in-month days
    # after the change, which the employment-window mask would otherwise blank to
    # OFF). Re-resolve each such day against the viewer's PersonHistory and splice
    # in their real schedule; days outside the viewer's own tenure entirely stay
    # OFF. In-month corrections keep the grid consistent with the stitched summary
    # computed above.
    if viewer_user_id is not None and session is not None:
        from .person_history import get_employment_period, get_user_person_id

        corrections: dict[int, list] = {}
        for pad_date in days_by_date:
            pad_position = get_user_person_id(session, viewer_user_id, on_date=pad_date)
            # A None or same-position resolution needs no correction: the main
            # fetch already produced (and correctly masked) that position's day.
            if pad_position is None or pad_position == person_id:
                continue
            pad_start, pad_end = get_employment_period(session, viewer_user_id, pad_position)
            if pad_date < pad_start or (pad_end is not None and pad_date > pad_end):
                # Padding date is outside the viewer's tenure at that position
                # (departed, or a successor now holds it): keep it OFF.
                continue
            corrections.setdefault(pad_position, []).append(pad_date)

        for pad_position, pad_dates in corrections.items():
            refetched = generate_period_data(
                min(pad_dates), max(pad_dates), pad_position, session=session, user_wages=user_wages
            )
            refetched_by_date = {d["date"]: d for d in refetched}
            for pad_date in pad_dates:
                src = refetched_by_date.get(pad_date)
                if src is None:
                    continue
                pad_data = days_by_date[pad_date]
                pad_data["shift"] = src.get("shift")
                pad_data["rotation_week"] = src.get("rotation_week")
                pad_data["rotation_length"] = src.get("rotation_length")
                pad_data["hours"] = src.get("hours", 0.0)
                pad_data["start"] = src.get("start")
                pad_data["end"] = src.get("end")
                pad_data["weekday_name"] = src.get("weekday_name")
                pad_data["partial_absence"] = src.get("partial_absence")
                _attach_coworkers(pad_data, src, pad_position)

    # Build grid structure (list of weeks, each week = 7 days)
    grid = []
    current_date = grid_start

    while current_date <= grid_end:
        week = []
        for _ in range(7):
            day_data = days_by_date.get(
                current_date,
                {
                    "date": current_date,
                    "shift": None,
                    "is_current_month": False,
                    "rotation_week": None,
                    "rotation_length": None,
                    "hours": 0.0,
                    "start": None,
                    "end": None,
                    "weekday_name": "",
                },
            )
            week.append(day_data)
            current_date += timedelta(days=1)
        grid.append(week)

    return {
        "summary": month_summary,
        "grid": grid,
    }


def _process_day_for_summary(
    day: dict,
    combined_rules: list,
    base_salary: int,
    totals: dict,
    ob_rate_overrides: dict[str, int] | None = None,
) -> dict:
    """Processar en dag och uppdaterar totaler."""
    hours = day.get("hours", 0.0)
    shift = day.get("shift")
    start = day.get("start")
    end = day.get("end")

    # Linked-substitute days (issue #290) are priced as hourly work: the OB base
    # is the substitute's hourly wage as a monthly equivalent (hourly x 173.33,
    # the same primitive as HOURLY users) and the user's own rate overrides do
    # not apply. The month's base_salary is never touched; the base pay for the
    # day is added separately below.
    is_substitute_day = bool(day.get("is_substitute"))
    substitute_wage = day.get("substitute_hourly_wage") or 0

    # Calculate OB if applicable (shared gate with the personal day view, issue #206)
    if is_substitute_day:
        ob_hours, ob_pay, ob_hours_by_day = compute_day_ob_pay(
            day, combined_rules, int(substitute_wage * _MONTHLY_HOURS), None
        )
    else:
        ob_hours, ob_pay, ob_hours_by_day = compute_day_ob_pay(day, combined_rules, base_salary, ob_rate_overrides)

    # Base pay for a worked substitute shift: hours x hourly wage, on top of the
    # (untouched) monthly base. OT substitute days are priced via ot_pay instead
    # and OC/absence days carry no base pay.
    if is_substitute_day and shift and shift.code in ("N1", "N2", "N3") and hours:
        substitute_base = hours * float(substitute_wage)
        totals["brutto_pay"] += substitute_base
        totals["substitute_base_pay"] = totals.get("substitute_base_pay", 0.0) + substitute_base
        totals["substitute_hours"] = totals.get("substitute_hours", 0.0) + hours

    # Compute midnight-crossing metadata (used for per-calendar-day OB aggregation)
    from datetime import datetime as _dt
    from datetime import time as _time
    from datetime import timedelta as _td

    date_next_day = None
    weekday_name_next_day = None
    hours_this_day = hours
    hours_next_day = 0.0
    if start and end and end.date() > start.date():
        midnight = _dt.combine(end.date(), _time(0, 0))
        hours_this_day = max((midnight - start).total_seconds() / 3600.0, 0.0)
        hours_next_day = max((end - midnight).total_seconds() / 3600.0, 0.0)
        date_next_day = end.date()
        weekday_name_next_day = weekday_names[date_next_day.weekday()]

    # Update totals (exclude OC from shifts and hours)
    if shift and shift.code != "OC":
        totals["total_hours"] += hours

    # "Antal pass" counts actual worked shifts only: day/evening/night and overtime.
    # On-call standby (OC) and all leave/absence days (OFF, SEM, SICK, VAB, LEAVE) are excluded.
    if shift and shift.code in ("N1", "N2", "N3", "OT"):
        totals["num_shifts"] += 1

    for code, h in ob_hours.items():
        totals["ob_hours"][code] = totals["ob_hours"].get(code, 0.0) + h

    for code, p in ob_pay.items():
        totals["ob_pay"][code] = totals["ob_pay"].get(code, 0.0) + p
        totals["brutto_pay"] += p

    # Add on-call and overtime
    oncall_pay = day.get("oncall_pay", 0.0)
    oncall_details = day.get("oncall_details", {})
    oncall_hours = oncall_details.get("total_hours", 0.0) if oncall_details else 0.0
    ot_pay = day.get("ot_pay", 0.0)
    ot_hours = day.get("ot_hours", 0.0)

    # Compute per-calendar-day OT hours for midnight-crossing overtime shifts
    ot_details_data = day.get("ot_details", {})
    ot_hours_by_day: dict = {}
    if ot_hours > 0 and ot_details_data:
        _start_str = str(ot_details_data.get("start_time", ""))
        _end_str = str(ot_details_data.get("end_time", ""))
        if _start_str and _end_str:
            try:
                _day_date = day["date"]
                _sh, _sm = int(_start_str[:2]), int(_start_str[3:5])
                _eh, _em = int(_end_str[:2]), int(_end_str[3:5])
                _ot_start = _dt(_day_date.year, _day_date.month, _day_date.day, _sh, _sm)
                _ot_end = _dt(_day_date.year, _day_date.month, _day_date.day, _eh, _em)
                if _ot_end <= _ot_start:
                    _ot_end += _td(days=1)
                if _ot_end.date() > _ot_start.date():
                    _midnight = _dt.combine(_ot_end.date(), _time(0, 0))
                    _ot_this = max((_midnight - _ot_start).total_seconds() / 3600.0, 0.0)
                    _ot_next = max((_ot_end - _midnight).total_seconds() / 3600.0, 0.0)
                    ot_hours_by_day = {_ot_start.date(): _ot_this, _ot_end.date(): _ot_next}
                else:
                    ot_hours_by_day = {_day_date: ot_hours}
            except (ValueError, IndexError, KeyError):
                ot_hours_by_day = {day["date"]: ot_hours}
    elif ot_hours > 0:
        ot_hours_by_day = {day["date"]: ot_hours}

    totals["brutto_pay"] += oncall_pay + ot_pay
    totals["oncall_pay"] += oncall_pay
    totals["oncall_hours"] += oncall_hours
    totals["ot_pay"] += ot_pay
    totals["ot_hours"] = totals.get("ot_hours", 0.0) + ot_hours
    totals["total_hours"] += ot_hours

    return {
        "date": day["date"],
        "weekday_name": day["weekday_name"],
        "shift": shift,
        "original_shift": day.get("original_shift"),
        "rotation_week": day.get("rotation_week"),
        "hours": hours,
        "ob_hours": ob_hours,
        "ob_pay": ob_pay,
        "ob_hours_by_day": ob_hours_by_day,
        "hours_this_day": hours_this_day,
        "hours_next_day": hours_next_day,
        "date_next_day": date_next_day,
        "weekday_name_next_day": weekday_name_next_day,
        "oncall_pay": oncall_pay,
        "oncall_details": day.get("oncall_details", {}),
        "ot_pay": ot_pay,
        "ot_hours": day.get("ot_hours", 0.0),
        "ot_hours_by_day": ot_hours_by_day,
        "ot_details": day.get("ot_details", {}),
        "start": start,
        "end": end,
        "partial_absence": day.get("partial_absence"),
        "is_substitute": day.get("is_substitute"),
        "substitute_hourly_wage": day.get("substitute_hourly_wage"),
    }


def _build_payment_month_mapping(year: int) -> list[dict]:
    """
    Build mapping of payment months for a year.

    For year 2026, this returns 12 entries representing:
    - Payment in Jan 2026 for work in Dec 2025
    - Payment in Feb 2026 for work in Jan 2026
    - ...
    - Payment in Dec 2026 for work in Nov 2026

    Args:
        year: The payment year to build mappings for

    Returns:
        List of 12 dicts with:
            - payment_month: Month when payment is made (1-12)
            - payment_year: Year when payment is made
            - payment_date: Actual payment date (date object)
            - work_month: Month when work was performed (1-12)
            - work_year: Year when work was performed
    """
    from app.core.utils import calculate_payment_date

    mappings = []

    for payment_month in range(1, 13):
        # Calculate which work month this payment represents
        if payment_month == 1:
            work_month = 12
            work_year = year - 1
        else:
            work_month = payment_month - 1
            work_year = year

        payment_date = calculate_payment_date(work_year, work_month)

        mappings.append(
            {
                "payment_month": payment_month,
                "payment_year": year,
                "payment_date": payment_date,
                "work_month": work_month,
                "work_year": work_year,
            }
        )

    return mappings


def _records_overlapping_work_month(records: list[dict], month_first, month_last) -> list[tuple]:
    """Return (record, seg_from, seg_to) for employment records overlapping a work month.

    seg_from/seg_to are the record's employment range clamped to the month.
    Records must be sorted by effective_from ascending, so the first returned
    segment is the position the user held at the start of their month.
    """
    import datetime as dt

    segments = []
    for record in records:
        record_end = record["effective_to"] if record["effective_to"] is not None else dt.date.max
        if record["effective_from"] <= month_last and record_end >= month_first:
            segments.append((record, max(record["effective_from"], month_first), min(record_end, month_last)))
    return segments


def _stitch_user_month_days(
    session,
    work_year: int,
    work_month: int,
    segments: list[tuple],
    user_wages: dict[int, int] | None,
    month_rates: dict | None,
) -> list[dict]:
    """Build one day list for a user's work month across the position(s) they held.

    Generates each overlapping position's month data, masks it to the record's
    clamped tenure segment (masking is skipped when the record covers the whole
    month), and for a mid-month move stitches a single list by taking each
    date's day dict from the segment covering that date, falling back to the
    first segment's masked OFF day for dates no record covers (employment gap).
    """
    import calendar
    import datetime as dt

    from app.core.schedule.period import mask_days_to_employment

    month_first = dt.date(work_year, work_month, 1)
    month_last = dt.date(work_year, work_month, calendar.monthrange(work_year, work_month)[1])

    masked_lists: list[tuple] = []
    for record, seg_from, seg_to in segments:
        position = record["person_id"]
        rates_map = {position: month_rates} if month_rates else None
        days = generate_month_data(
            work_year, work_month, position, session=session, user_wages=user_wages, user_rates_map=rates_map
        )
        if seg_from > month_first or seg_to < month_last:
            days = mask_days_to_employment(days, seg_from, seg_to)
        masked_lists.append((seg_from, seg_to, days))

    if len(masked_lists) == 1:
        return masked_lists[0][2]

    by_date = [(seg_from, seg_to, {d["date"]: d for d in days}) for seg_from, seg_to, days in masked_lists]
    stitched = []
    for day in masked_lists[0][2]:
        day_date = day["date"]
        chosen = day  # fallback: the first segment's masked OFF day
        for seg_from, seg_to, date_map in by_date:
            if seg_from <= day_date <= seg_to and day_date in date_map:
                chosen = date_map[day_date]
                break
        stitched.append(chosen)
    return stitched


def stitch_user_week_days(
    session,
    year: int,
    week: int,
    viewer_user_id: int,
    base_position: int,
    employment_start,
    employment_end,
) -> list[dict] | None:
    """Build a user's week across the position(s) they held, stitching a mid-week move.

    Mirrors _stitch_user_month_days for the week view: each overlapping position's
    week is built and masked to the record's clamped tenure segment, then a single
    seven-day list is stitched by taking each date from the segment that covers it,
    falling back to the base (single-position) build for dates no segment covers
    (an employment gap). Returns None when the user held a single position all week,
    so the caller keeps the plain single-position build unchanged.
    """
    import datetime as dt

    from app.core.schedule.period import build_week_data
    from app.core.schedule.person_history import get_user_history

    records = sorted(get_user_history(session, viewer_user_id), key=lambda r: r["effective_from"])
    if not records:
        return None

    monday = dt.date.fromisocalendar(year, week, 1)
    sunday = monday + dt.timedelta(days=6)
    segments = _records_overlapping_work_month(records, monday, sunday)
    if len(segments) <= 1:
        return None

    base = build_week_data(
        year,
        week,
        person_id=base_position,
        session=session,
        include_coworkers=True,
        employment_start=employment_start,
        employment_end=employment_end,
    )
    seg_maps = []
    for record, seg_from, seg_to in segments:
        seg_days = build_week_data(
            year,
            week,
            person_id=record["person_id"],
            session=session,
            include_coworkers=True,
            employment_start=seg_from,
            employment_end=seg_to,
        )
        seg_maps.append((seg_from, seg_to, {d["date"]: d for d in seg_days}))

    stitched = []
    for day in base:
        chosen = day
        for seg_from, seg_to, dmap in seg_maps:
            if seg_from <= day["date"] <= seg_to and day["date"] in dmap:
                chosen = dmap[day["date"]]
                break
        stitched.append(chosen)
    return stitched


def summarize_year_for_person(
    year: int,
    person_id: int,
    session=None,
    current_user=None,
    wage_user_id: int | None = None,
    employment_user_id: int | None = None,
) -> dict:
    """
    Bygger årsöversikt för en person baserat på UTBETALNINGS-månader.

    För år 2026 returneras 12 månader som representerar:
    - Jan-utbetalning (för dec 2025 arbete)
    - Feb-utbetalning (för jan 2026 arbete)
    - ...
    - Dec-utbetalning (för nov 2026 arbete)

    Args:
        year: År för utbetalningar
        person_id: Rotationsposition (1-10) för schemaberäkning
        session: SQLAlchemy session
        current_user: Inloggad användare (för att filtrera på anställningsperiod)
        wage_user_id: Användar-ID för löneberäkning (om annan än person_id)
        employment_user_id: When set, each WORK month is built from this user's
            full PersonHistory record list regardless of the viewer's role: a
            month is skipped when no record overlaps it, computed from the held
            position when one record does, and stitched across positions when a
            mid-month move splits it. Days outside the user's tenure are masked
            to OFF. This is the user-scoped path (e.g. viewing /year/<user_id>),
            so an admin looking at another user still sees only that user's
            employed months, and the passed ``person_id`` only matters for the
            no-history fallback. When None, the legacy viewer-based filter
            applies (non-admin viewers filtered against their own employment
            with the payment-month overlap rules).

    Returns:
        Dict med 'months' (lista med 12 månadsdictar) och 'year_summary'

    Note:
        För vanliga användare filtreras månaderna baserat på anställningsperiod.
        Admins ser alla månader (utom när employment_user_id är satt).
    """
    import datetime as dt

    from app.database.database import UserRole

    settings = get_settings()
    user_wages = get_all_user_wages(session)

    # Resolve user for per-month rate lookups
    _rate_user = None
    if session:
        from app.core.rates import get_user_rates
        from app.database.database import User

        _uid = wage_user_id if wage_user_id is not None else person_id
        _rate_user = session.query(User).filter(User.id == _uid).first()

    # When scoping to a specific user, load their FULL employment record list up
    # front so the year covers every position they held: a position change must
    # not truncate the year to one (position, employment period) pairing, and a
    # mid-month move must not credit a whole month's OB/hours to one position.
    emp_records = None
    if employment_user_id is not None:
        import calendar  # used by the per-month overlap bounds in the loop below

        from app.core.schedule.person_history import get_user_history

        emp_records = sorted(get_user_history(session, employment_user_id), key=lambda r: r["effective_from"])
        if not emp_records:
            # No history: mirror get_employment_period's fallback (employed at
            # the passed position from rotation start, open-ended) so legacy
            # users without PersonHistory rows keep their full year.
            from app.core.schedule.core import get_rotation_start_date

            emp_records = [{"person_id": person_id, "effective_from": get_rotation_start_date(), "effective_to": None}]

    # Bygg kartläggning av utbetalnings-månader
    payment_mappings = _build_payment_month_mapping(year)

    months = []
    for mapping in payment_mappings:
        work_year = mapping["work_year"]
        work_month = mapping["work_month"]
        payment_date = mapping["payment_date"]

        # User-scoped path: collect the user's employment segments overlapping
        # this WORK month. No overlap means the user held no position that
        # month, so it is skipped (this replaces the legacy post-loop filter).
        month_segments = None
        if emp_records is not None:
            month_first = dt.date(work_year, work_month, 1)
            month_last = dt.date(work_year, work_month, calendar.monthrange(work_year, work_month)[1])
            month_segments = _records_overlapping_work_month(emp_records, month_first, month_last)
            if not month_segments:
                continue

        # Special case: Dec 2025 - använd bara grundlön (rotation startade inte förrän 2026)
        if work_year == 2025 and work_month == 12:
            from app.database.database import User

            # Use wage_user_id for wage lookup if provided
            uid_for_wages = wage_user_id if wage_user_id is not None else person_id

            base_salary = get_effective_monthly_wage(
                session, uid_for_wages, settings.monthly_salary, effective_date=dt.date(2025, 12, 1)
            )

            # Load tax table for correct net-pay calculation
            tax_table = None
            if session:
                user = session.query(User).filter(User.id == uid_for_wages).first()
                if user and user.tax_table:
                    tax_table = user.tax_table

            # Calculate net pay using the correct tax table for the payment year
            netto_pay = base_salary - _calculate_tax(base_salary, tax_table, payment_year=mapping["payment_year"])

            # User-scoped path: label the row with the position the user held in
            # Dec 2025; the legacy path keeps the passed position unchanged.
            dec_person_id = month_segments[0][0]["person_id"] if month_segments else person_id

            m = {
                "year": 2025,
                "month": 12,
                "person_id": dec_person_id,
                "total_hours": 0.0,
                "num_shifts": 0,
                "ob_hours": {},
                "ob_pay": {},
                "oncall_pay": 0.0,
                "ot_pay": 0.0,
                "brutto_pay": base_salary,
                "netto_pay": netto_pay,
                "absence_deduction": 0.0,
                "sick_days": 0,
                "vab_days": 0,
                "leave_days": 0,
                "off_days": 0,
            }
        elif month_segments is not None:
            # User-scoped path: build the month from the position(s) the user
            # actually held, each masked to its tenure segment, stitched into
            # one day list for a mid-month move.
            _month_rates = None
            if _rate_user:
                _month_effective = dt.date(work_year, work_month, 1)
                _month_rates = get_user_rates(_rate_user, session=session, effective_date=_month_effective)

            stitched_days = _stitch_user_month_days(
                session, work_year, work_month, month_segments, user_wages, _month_rates
            )

            # person_id for the summary is the position held at the month start
            # (the first overlapping record): name resolution and the month's
            # person_id field reflect where the user's month began.
            m = summarize_month_for_person(
                work_year,
                work_month,
                month_segments[0][0]["person_id"],
                session=session,
                user_wages=user_wages,
                year_days=stitched_days,
                payment_year=mapping["payment_year"],
                wage_user_id=wage_user_id,
            )
        else:
            # Generate per-month data with temporal rates for correct on-call/OT
            _month_rates_map = None
            if _rate_user:
                _month_effective = dt.date(work_year, work_month, 1)
                _month_rates = get_user_rates(_rate_user, session=session, effective_date=_month_effective)
                _month_rates_map = {person_id: _month_rates}

            month_days = generate_month_data(
                work_year,
                work_month,
                person_id,
                session=session,
                user_wages=user_wages,
                user_rates_map=_month_rates_map,
            )

            # Sammanfatta baserat på ARBETS-månad, inte utbetalnings-månad
            m = summarize_month_for_person(
                work_year,
                work_month,
                person_id,
                session=session,
                user_wages=user_wages,
                year_days=month_days,
                payment_year=mapping["payment_year"],
                wage_user_id=wage_user_id,
            )

        # Attach payment metadata
        m["payment_date"] = payment_date
        m["payment_month"] = mapping["payment_month"]
        m["payment_year"] = mapping["payment_year"]

        # Compute total OB pay
        ob_pay = m.get("ob_pay", {}) or {}
        total_ob = sum(float(ob_pay.get(code, 0.0) or 0.0) for code in ("OB1", "OB2", "OB3", "OB4", "OB5"))
        m["total_ob"] = total_ob

        months.append(m)

    # Filter months by employment period (legacy viewer-based path only): the
    # user-scoped path already skipped work months without an employment record
    # overlap inside the loop above.
    filter_user_id = None
    if employment_user_id is None and current_user and current_user.role != UserRole.ADMIN:
        filter_user_id = current_user.id

    if filter_user_id is not None:
        import calendar

        from app.core.schedule.person_history import get_employment_period

        start_date, end_date = get_employment_period(session, filter_user_id, person_id)

        # Filter on both WORK month and PAYMENT month:
        # - Work month must not end before employment start (avoids showing
        #   predecessor's data when salary is paid on trailing basis)
        # - Payment month must overlap with employment period (tax year view)
        filtered_months = []
        for m in months:
            # Skip if work month ended before employment started
            work_last_day = calendar.monthrange(m["year"], m["month"])[1]
            work_month_end = dt.date(m["year"], m["month"], work_last_day)
            if start_date > work_month_end:
                continue

            # Legacy payment-month overlap (keeps a departed viewer's final
            # trailing payment visible).
            pay_year = m["payment_year"]
            pay_month = m["payment_month"]
            month_start = dt.date(pay_year, pay_month, 1)
            last_day = calendar.monthrange(pay_year, pay_month)[1]
            month_end = dt.date(pay_year, pay_month, last_day)

            # Check if there's ANY overlap between employment period and payment month
            if start_date > month_end:
                continue
            if end_date and end_date < month_start:
                continue

            filtered_months.append(m)

        months = filtered_months

    # Årssumman inkluderar alla 12 månader (utbetalningar under året)
    year_summary = _build_year_summary(months)

    return {
        "months": months,
        "year_summary": year_summary,
    }


def apply_year_pay_adjustments(months: list[dict], year_summary: dict, user, year: int, session) -> dict | None:
    """Fold the vacation supplement and the employment transition into a year's pay.

    Mutates ``months`` and ``year_summary`` in place: adds the per-month
    semestertillägg (0.8% salary + 0.5% variable per vacation day, computed by
    calculate_vacation_balance), folds it into gross/net, injects the consultant
    vacation payout into the transition month and appends a row for the direct
    employer's share, then recomputes the year totals and averages.

    Both /year/<id> and /statistics/<id> show these figures. They each used to
    do this arithmetic themselves and disagreed about the same person's gross
    and net, so it lives here and both call it.

    Returns the vacation balance dict (the year view renders it), or None when
    it could not be calculated.
    """
    from app.core.schedule.transition import calculate_transition_month_summary
    from app.core.schedule.vacation import calculate_vacation_balance, fold_vacation_supplement_into_pay

    vacation_pay = None
    try:
        vacation_pay = calculate_vacation_balance(user, year, session)
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
                m["brutto_pay"], m["netto_pay"] = fold_vacation_supplement_into_pay(
                    m.get("brutto_pay", 0), m.get("netto_pay", 0), m["vacation_supplement"]
                )

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
        logger.warning("Vacation supplement could not be applied for user %s, year %s", user.id, year, exc_info=True)

    if not user.employment_transition or user.employment_transition.transition_date.year != year:
        return vacation_pay

    try:
        transition_data = calculate_transition_month_summary(user.employment_transition, user, session)
    except Exception:
        logger.warning("Employment transition could not be applied for user %s, year %s", user.id, year, exc_info=True)
        return vacation_pay

    vac_payout = float(transition_data["consultant_employer"]["vacation_payout"]["total"])
    direct_salary = float(transition_data["direct_employer"]["base_salary"])
    t_year = transition_data["transition_year"]
    t_month = transition_data["transition_month"]

    for i, m in enumerate(months):
        if m["payment_date"].year != t_year or m["payment_date"].month != t_month:
            continue

        brutto = float(m.get("brutto_pay") or 0)
        netto = float(m.get("netto_pay") or 0)
        tax_ratio = (netto / brutto) if brutto > 0 else 0.72

        # Add vacation payout to Sem.till. and Gross on the trailing consultant row
        m["vacation_supplement"] = round((m.get("vacation_supplement") or 0) + vac_payout, 0)
        m["brutto_pay"] = round(brutto + vac_payout, 0)
        m["netto_pay"] = round(netto + vac_payout * tax_ratio, 0)

        # Extra row: direct employer innestående base salary. Same payslip month
        # as the row above, which is how consumers can fold the two together.
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

        # Update year totals and averages: average uses the original month count
        year_summary["total_brutto"] = sum(float(m2.get("brutto_pay") or 0) for m2 in months)
        year_summary["total_netto"] = sum(float(m2.get("netto_pay") or 0) for m2 in months)
        year_summary["avg_brutto"] = round(year_summary["total_brutto"] / original_count, 0)
        year_summary["avg_netto"] = round(year_summary["total_netto"] / original_count, 0)
        if "total_vacation_supplement" in year_summary:
            year_summary["total_vacation_supplement"] = sum(float(m2.get("vacation_supplement") or 0) for m2 in months)
            year_summary["avg_vacation_supplement"] = round(
                year_summary["total_vacation_supplement"] / original_count, 0
            )
        break

    return vacation_pay


def _report_row_from_summary(summary: dict, is_substitute: bool) -> dict:
    """Flatten a month summary (agent or substitute) into a single report row.

    Pulls the figures the monthly report needs: hours, overtime, on-call, OB total
    and absence days per type. The report tracks time only, not salary.
    """
    ob_hours_map = summary.get("ob_hours", {}) or {}

    def _ob_sum(*codes: str) -> float:
        return round(sum(float(ob_hours_map.get(c, 0.0) or 0.0) for c in codes), 1)

    # Hours = worked shift hours (day/evening/night) plus overtime, each counted once.
    # summary["total_hours"] cannot be used directly: it double-counts non-extension OT.
    ot_hours = float(summary.get("ot_hours", 0.0) or 0.0)
    worked_pass_hours = sum(
        float(d.get("hours", 0.0) or 0.0)
        for d in (summary.get("days") or [])
        if d.get("shift") is not None and getattr(d["shift"], "code", None) in ("N1", "N2", "N3")
    )

    return {
        "person_name": summary.get("person_name", ""),
        "person_id": summary.get("person_id"),
        "substitute_id": summary.get("substitute_id"),
        "is_substitute": is_substitute,
        "num_shifts": summary.get("num_shifts", 0) or 0,
        "total_hours": round(worked_pass_hours + ot_hours, 1),
        "ot_hours": round(ot_hours, 1),
        "oncall_hours": round(summary.get("oncall_hours", 0.0) or 0.0, 1),
        # OB split into pay-code groups: evening, night, weekend, major holiday
        "ob_kvall": _ob_sum("OB1"),
        "ob_natt": _ob_sum("OB2"),
        "ob_helg": _ob_sum("OB3", "OB4"),
        "ob_storhelg": _ob_sum("OB5"),
        "sick_days": summary.get("sick_days", 0) or 0,
        "vab_days": summary.get("vab_days", 0) or 0,
        "leave_days": summary.get("leave_days", 0) or 0,
        "off_days": summary.get("off_days", 0) or 0,
        "parental_days": summary.get("parental_days", 0) or 0,
        "vacation_days": summary.get("vacation_days", 0) or 0,
    }


def build_month_report(year: int, month: int, session, fetch_tax_table: bool = False) -> list[dict]:
    """Build one report row per agent (rotation positions 1-10) plus substitutes.

    Reuses summarize_month_for_person for agents and build_substitute_month_summaries
    for substitutes, returning a flat list of rows ready for the report table / CSV.
    The report tracks time only, so tax lookups are skipped by default.
    """
    from .period import build_substitute_month_summaries

    user_wages = get_all_user_wages(session)

    rows = []
    for pid in range(1, 11):
        month_days = generate_month_data(year, month, pid, session=session, user_wages=user_wages)
        summary = summarize_month_for_person(
            year,
            month,
            pid,
            session=session,
            user_wages=user_wages,
            year_days=month_days,
            fetch_tax_table=fetch_tax_table,
            payment_year=year,
        )
        rows.append(_report_row_from_summary(summary, is_substitute=False))

    # exclude_linked_attributed: a linked substitute's worked/OT days already
    # count in the linked user's row above (issue #290 double-count guard).
    for sub_summary in build_substitute_month_summaries(
        year, month, session, include_overtime=True, exclude_linked_attributed=True
    ):
        rows.append(_report_row_from_summary(sub_summary, is_substitute=True))

    return rows


def _build_year_summary(months: list[dict]) -> dict:
    """Builds a yearly summary from monthly data."""
    month_count = len(months) or 1

    # Summera totaler
    total_netto = sum(m.get("netto_pay", 0.0) for m in months)
    total_brutto = sum(m.get("brutto_pay", 0.0) for m in months)
    total_shifts = sum(m.get("num_shifts", 0) for m in months)
    total_hours = sum(m.get("total_hours", 0.0) for m in months)
    total_ob = sum(m.get("total_ob", 0.0) for m in months)
    total_oncall = sum(m.get("oncall_pay", 0.0) for m in months)
    total_oncall_hours = sum(m.get("oncall_hours", 0.0) for m in months)
    total_ot = sum(m.get("ot_pay", 0.0) for m in months)
    total_absence_deduction = sum(m.get("absence_deduction", 0.0) for m in months)
    total_absence_hours = sum(m.get("absence_hours", 0.0) for m in months)
    total_sick_days = sum(m.get("sick_days", 0) for m in months)
    total_sick_hours = sum(m.get("sick_hours", 0.0) for m in months)
    total_sick_ob_pay = sum(m.get("sick_ob_pay", 0.0) for m in months)
    total_sick_ob_lost = sum(m.get("sick_ob_lost", 0.0) for m in months)
    total_sick_total_ob = sum(m.get("sick_total_ob", 0.0) for m in months)
    total_vab_days = sum(m.get("vab_days", 0) for m in months)
    total_vab_hours = sum(m.get("vab_hours", 0.0) for m in months)
    total_leave_days = sum(m.get("leave_days", 0) for m in months)
    total_leave_hours = sum(m.get("leave_hours", 0.0) for m in months)
    total_off_days = sum(m.get("off_days", 0) for m in months)
    total_off_hours = sum(m.get("off_hours", 0.0) for m in months)
    total_parental_days = sum(m.get("parental_days", 0) for m in months)
    total_parental_hours = sum(m.get("parental_hours", 0.0) for m in months)

    # Calculate deductions per type from monthly details
    sick_deduction = 0.0
    vab_deduction = 0.0
    leave_deduction = 0.0
    off_deduction = 0.0

    for m in months:
        details = m.get("absence_details", [])
        for detail in details:
            if detail["type"] == "SICK":
                sick_deduction += detail["deduction"]
            elif detail["type"] == "VAB":
                vab_deduction += detail["deduction"]
            elif detail["type"] == "LEAVE":
                leave_deduction += detail["deduction"]
            elif detail["type"] == "OFF":
                off_deduction += detail["deduction"]

    # OB per kod
    ob_codes = ["OB1", "OB2", "OB3", "OB4", "OB5"]
    ob_hours_by_code = {code: 0.0 for code in ob_codes}
    ob_pay_by_code = {code: 0.0 for code in ob_codes}

    for m in months:
        m_ob_hours = m.get("ob_hours", {}) or {}
        m_ob_pay = m.get("ob_pay", {}) or {}
        for code in ob_codes:
            ob_hours_by_code[code] += float(m_ob_hours.get(code, 0.0) or 0.0)
            ob_pay_by_code[code] += float(m_ob_pay.get(code, 0.0) or 0.0)

    return {
        "total_netto": total_netto,
        "total_brutto": total_brutto,
        "total_shifts": total_shifts,
        "total_hours": total_hours,
        "total_ob": total_ob,
        "total_oncall": total_oncall,
        "total_oncall_hours": total_oncall_hours,
        "total_ot": total_ot,
        "total_absence_deduction": total_absence_deduction,
        "total_absence_hours": total_absence_hours,
        "total_sick_days": total_sick_days,
        "total_sick_hours": total_sick_hours,
        "total_sick_ob_pay": total_sick_ob_pay,
        "total_sick_ob_lost": total_sick_ob_lost,
        "total_sick_total_ob": total_sick_total_ob,
        "total_vab_days": total_vab_days,
        "total_vab_hours": total_vab_hours,
        "total_leave_days": total_leave_days,
        "total_leave_hours": total_leave_hours,
        "total_off_days": total_off_days,
        "total_off_hours": total_off_hours,
        "total_parental_days": total_parental_days,
        "total_parental_hours": total_parental_hours,
        "sick_deduction": sick_deduction,
        "vab_deduction": vab_deduction,
        "leave_deduction": leave_deduction,
        "off_deduction": off_deduction,
        "avg_netto": total_netto / month_count,
        "avg_brutto": total_brutto / month_count,
        "avg_shifts": total_shifts / month_count,
        "avg_hours": total_hours / month_count,
        "avg_ob": total_ob / month_count,
        "avg_oncall": total_oncall / month_count,
        "avg_oncall_hours": total_oncall_hours / month_count,
        "avg_ot": total_ot / month_count,
        "avg_absence_deduction": total_absence_deduction / month_count,
        "avg_sick_total_ob": total_sick_total_ob / month_count,
        "avg_sick_ob_pay": total_sick_ob_pay / month_count,
        "ob_hours_by_code": ob_hours_by_code,
        "ob_pay_by_code": ob_pay_by_code,
        "total_ob_hours": sum(ob_hours_by_code.values()),
    }
