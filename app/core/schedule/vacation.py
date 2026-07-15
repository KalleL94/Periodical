"""Vacation management – week-based, day-level and balance calculations."""

import datetime
import math

from app.core.constants import PERSON_IDS
from app.core.logging_config import get_logger

logger = get_logger(__name__)


def _leave_dates_by_position(year: int, session, *, week_attr: str, absence_type) -> dict[int, set[datetime.date]]:
    """Bucket a year's leave dates by the rotation position held on each date.

    Shared implementation for vacation and parental leave. Both sources
    (week-based JSON on the User row and day-level Absence rows) are attributed
    per day to the position the holder occupied ON THAT DATE via PersonHistory,
    not the holder's current position. This mirrors the batch-fetch helpers in
    period.py so a vacation/parental date recorded before a position swap or
    succession stays on the position that was actually held then, and a week
    straddling a change splits day-by-day across the two positions.

    Args:
        week_attr: User attribute holding the week-based JSON ("vacation" or
            "parental_leave").
        absence_type: AbsenceType whose day-level rows are day-level leave.

    Returns:
        Dict med person_id (rotationsposition) -> set av datum
    """
    from app.core.schedule.period import _build_user_position_resolver
    from app.database.database import Absence, PersonHistory, User

    per_person: dict[int, set[datetime.date]] = {pid: set() for pid in PERSON_IDS}

    year_start = datetime.date(year, 1, 1)
    year_end = datetime.date(year, 12, 31)

    # Candidate holders: currently active users at a rotation position (covers
    # legacy users with no PersonHistory) plus every user who held a rotation
    # position at any point during the year (covers departed/future holders and
    # users who changed position within the year, e.g. via a swap). Their leave
    # would otherwise be missed entirely or bucketed onto their current position.
    active_users = session.query(User).filter(User.person_id.in_(PERSON_IDS), User.is_active).all()
    current_map = {u.id: u.person_id for u in active_users}
    candidate_ids: set[int] = set(current_map.keys())

    history_rows = (
        session.query(PersonHistory.user_id)
        .filter(
            PersonHistory.person_id.in_(PERSON_IDS),
            PersonHistory.effective_from <= year_end,
            (PersonHistory.effective_to.is_(None)) | (PersonHistory.effective_to >= year_start),
        )
        .distinct()
        .all()
    )
    candidate_ids.update(uid for (uid,) in history_rows)

    if not candidate_ids:
        return per_person

    users = session.query(User).filter(User.id.in_(candidate_ids)).all()

    # One PersonHistory read builds a date-aware (user_id, date) -> position
    # resolver; current_map is the fallback for legacy users without history.
    resolve_pos = _build_user_position_resolver(session, candidate_ids, current_map)

    # Week-based leave: expand each ISO week to its days and attribute per day.
    for user in users:
        by_year = getattr(user, week_attr, None) or {}
        weeks_for_year = by_year.get(str(year), []) or []
        for week in weeks_for_year:
            for day in range(1, 8):
                try:
                    d = datetime.date.fromisocalendar(year, week, day)
                except ValueError:
                    continue
                pos = resolve_pos(user.id, d)
                if pos in per_person:
                    per_person[pos].add(d)

    # Day-level leave from the Absence table, attributed to the row's own date.
    day_absences = (
        session.query(Absence)
        .filter(
            Absence.user_id.in_(candidate_ids),
            Absence.absence_type == absence_type,
            Absence.date >= year_start,
            Absence.date <= year_end,
        )
        .all()
    )
    for absence in day_absences:
        pos = resolve_pos(absence.user_id, absence.date)
        if pos in per_person:
            per_person[pos].add(absence.date)

    return per_person


def get_vacation_dates_for_year(year: int, session=None) -> dict[int, set[datetime.date]]:
    """
    Hämtar semesterdatum för alla personer för ett år.

    Merges both week-based vacation (User.vacation JSON) and
    day-level vacation (Absence records with type VACATION). Each date is
    attributed to the rotation position its holder actually occupied on that
    date (via PersonHistory), so a swap or succession does not move historical
    vacation onto the successor's column.

    Returns:
        Dict med person_id (rotationsposition) -> set av semesterdatum
    """
    from app.database.database import AbsenceType, SessionLocal

    _owned = session is None
    db = SessionLocal() if _owned else session
    try:
        return _leave_dates_by_position(year, db, week_attr="vacation", absence_type=AbsenceType.VACATION)
    finally:
        if _owned:
            db.close()


def get_parental_dates_for_year(year: int, session=None) -> dict[int, set[datetime.date]]:
    """
    Hämtar föräldraledighetsdatum för alla personer för ett år.

    Merges week-based parental leave (User.parental_leave JSON) and
    day-level parental leave (Absence records with type PARENTAL). Each date is
    attributed to the rotation position its holder actually occupied on that
    date (via PersonHistory), matching the vacation handling.

    Returns:
        Dict med person_id (rotationsposition) -> set av datum
    """
    from app.database.database import AbsenceType, SessionLocal

    _owned = session is None
    db = SessionLocal() if _owned else session
    try:
        return _leave_dates_by_position(year, db, week_attr="parental_leave", absence_type=AbsenceType.PARENTAL)
    finally:
        if _owned:
            db.close()


# ---------------------------------------------------------------------------
# Vacation balance calculation
# ---------------------------------------------------------------------------


def get_vacation_year_boundaries(reference_year: int, start_month: int) -> tuple[datetime.date, datetime.date]:
    """
    Calculate the vacation year boundaries for a given reference year and break month.

    For start_month=4 (April) and reference_year=2026:
      Vacation year: 2026-04-01 → 2027-03-31

    Returns:
        (year_start, year_end) as datetime.date
    """
    year_start = datetime.date(reference_year, start_month, 1)
    if start_month == 1:
        year_end = datetime.date(reference_year, 12, 31)
    else:
        year_end = datetime.date(reference_year + 1, start_month, 1) - datetime.timedelta(days=1)
    return year_start, year_end


def _count_weekdays_in_vacation_weeks(
    weeks: list[int],
    iso_year: int,
    period_start: datetime.date,
    period_end: datetime.date,
    off_dates: set[datetime.date] | None = None,
) -> int:
    """
    Count vacation days consumed by the given ISO weeks within [period_start, period_end].

    When off_dates is provided, a week consumes only the days the employee was actually
    scheduled to work (all seven weekdays minus OFF-shift days), so week-based vacation is
    counted the same way as day-level vacation. Without off_dates it falls back to a flat
    Mon-Fri (5 days/week).
    """
    count = 0
    # With a known schedule, consider all 7 days and drop the OFF ones; otherwise Mon-Fri.
    day_range = range(1, 8) if off_dates is not None else range(1, 6)
    for week in weeks:
        for iso_day in day_range:
            try:
                d = datetime.date.fromisocalendar(iso_year, week, iso_day)
            except ValueError:
                continue
            if not (period_start <= d <= period_end):
                continue
            if off_dates is not None and d in off_dates:
                continue
            count += 1
    return count


def _scheduled_off_dates(user, start: datetime.date, end: datetime.date) -> set[datetime.date]:
    """Return the OFF-shift dates for a user across [start, end] based on the rotation."""
    from app.core.schedule.core import determine_shift_for_date

    off: set[datetime.date] = set()
    d = start
    while d <= end:
        shift, _ = determine_shift_for_date(d, user.rotation_person_id)
        if shift and shift.code == "OFF":
            off.add(d)
        d += datetime.timedelta(days=1)
    return off


def count_vacation_days_used(
    user_id: int,
    year_start: datetime.date,
    year_end: datetime.date,
    db,
    vacation_json: dict | None = None,
    off_dates: set[datetime.date] | None = None,
) -> dict:
    """
    Count vacation days consumed in a period from both sources.

    Args:
        user_id: The user to count for
        year_start: Start of vacation year (inclusive)
        year_end: End of vacation year (inclusive)
        db: Database session
        vacation_json: User.vacation dict (if already loaded), avoids extra query

    Returns:
        {"week_based": int, "day_level": int, "total": int}
    """
    from app.database.database import Absence, AbsenceType, User

    # Get vacation JSON if not provided
    if vacation_json is None:
        user = db.query(User).filter(User.id == user_id).first()
        vacation_json = user.vacation if user else {}

    vacation_json = vacation_json or {}

    # Count weekdays from week-based vacation
    week_based = 0
    # The vacation year may span two calendar years, so check both
    calendar_years = set()
    d = year_start
    while d <= year_end:
        calendar_years.add(d.year)
        # Jump forward by month to avoid iterating every day
        if d.month == 12:
            d = datetime.date(d.year + 1, 1, 1)
        else:
            d = datetime.date(d.year, d.month + 1, 1)

    for cal_year in calendar_years:
        weeks = vacation_json.get(str(cal_year), []) or []
        if weeks:
            week_based += _count_weekdays_in_vacation_weeks(weeks, cal_year, year_start, year_end, off_dates)

    # Count day-level vacation from absences (exclude OFF-shift days)
    absences = (
        db.query(Absence)
        .filter(
            Absence.user_id == user_id,
            Absence.absence_type == AbsenceType.VACATION,
            Absence.date >= year_start,
            Absence.date <= year_end,
        )
        .all()
    )
    day_level = sum(1 for a in absences if off_dates is None or a.date not in off_dates)

    return {
        "week_based": week_based,
        "day_level": day_level,
        "total": week_based + day_level,
    }


def _calculate_prorated_days(
    employment_start: datetime.date,
    earning_year_start: datetime.date,
    earning_year_end: datetime.date,
    full_year_days: int,
) -> int:
    """
    Pro-rate vacation for partial earning year (day-based).

    Per Handelns avtal §9 punkt 2:
      D = ceil(A × B / C)
      A = full_year_days (e.g. 25)
      B = employment days within earning year
      C = total calendar days in earning year
    """
    effective_start = max(employment_start, earning_year_start)
    if effective_start > earning_year_end:
        return 0

    employment_days = (earning_year_end - effective_start).days + 1
    total_days = (earning_year_end - earning_year_start).days + 1

    return math.ceil(full_year_days * employment_days / total_days)


def get_saved_days_balance(user, target_year: int) -> dict:
    """
    Sum saved vacation days from previous years available in the given vacation year.

    Per Swedish semesterlag §18, saved days are valid for up to 5 years.

    Args:
        user: User ORM object with vacation_saved JSON field
        target_year: The vacation year to check saved days for

    Returns:
        {"total_saved": int, "breakdown": [{"year": str, "days": int}, ...]}
    """
    vacation_saved = user.vacation_saved or {}
    total = 0
    breakdown = []

    for y in range(target_year - 5, target_year):
        entry = vacation_saved.get(str(y))
        if entry and entry.get("saved", 0) > 0:
            days = entry["saved"]
            total += days
            breakdown.append({"year": str(y), "days": days})

    return {"total_saved": total, "breakdown": breakdown}


def close_vacation_year(user, target_year: int, remaining_own: int, pay: dict, db) -> dict:
    """
    Close a vacation year: save up to 5 of the year's own remaining days and pay out the rest.

    Semesterersattning per Handelns avtal 9.5:
      4.6% of monthly salary + 0.8% of monthly salary + 0.5% of variable per day
      = 5.4% of monthly salary + 0.5% of variable per day

    Days saved in earlier years are not part of the close: they stay saved (valid
    for five years, semesterlagen paragraph 18) unless the year used more than its
    own entitlement, in which case the overuse consumes saved days oldest year first.

    Args:
        user: User ORM object
        target_year: The vacation year to close
        remaining_own: The year's own remaining days (entitled - used); may be negative
        pay: Result dict from calculate_vacation_pay()
        db: Database session

    Returns:
        {"saved": int, "paid_out": int, "payout_amount": float, "payout_per_day": float}
    """
    from sqlalchemy.orm.attributes import flag_modified

    monthly_salary = pay.get("monthly_salary", 0)
    supplement_per_day = pay.get("supplement_per_day", 0)
    payout_pct = pay.get("payout_pct", 0.046)

    # Vacation compensation = payout_pct base + vacation supplement
    payout_per_day = round(monthly_salary * payout_pct + supplement_per_day, 2)

    saved = dict(user.vacation_saved or {})

    if remaining_own <= 0:
        days_saved = 0
        days_paid_out = 0
        # Overuse beyond the year's own entitlement consumes previously saved
        # days, oldest year first.
        overuse = -remaining_own
        if overuse > 0:
            for y in sorted((k for k in saved if str(k).isdigit() and int(k) < target_year), key=int):
                entry = dict(saved[y])
                available = entry.get("saved", 0)
                take = min(available, overuse)
                if take > 0:
                    entry["saved"] = available - take
                    saved[y] = entry
                    overuse -= take
                if overuse <= 0:
                    break
    else:
        days_saved = min(remaining_own, 5)
        days_paid_out = max(remaining_own - 5, 0)

    payout_amount = round(days_paid_out * payout_per_day, 2)

    closed_data = {
        "saved": days_saved,
        "paid_out": days_paid_out,
        "payout_amount": payout_amount,
        "payout_per_day": payout_per_day,
    }

    saved[str(target_year)] = closed_data
    user.vacation_saved = saved
    flag_modified(user, "vacation_saved")
    db.commit()

    return closed_data


def calculate_vacation_balance(user, target_year: int, db, off_dates: set[datetime.date] | None = None) -> dict:
    """
    Calculate complete vacation balance for a user in a given vacation year.

    Includes saved days from previous years, projection for open years,
    and auto-closes past vacation years (lazy close on first access).

    Args:
        user: User ORM object (must have vacation fields)
        target_year: The reference year for the vacation year
        db: Database session

    Returns:
        {
            "entitled_days": int,
            "used_days": int,
            "remaining_days": int,       # entitled + saved - used (total remaining)
            "year_start": date,
            "year_end": date,
            "earning_year_start": date,
            "earning_year_end": date,
            "is_first_year": bool,
            "week_based_used": int,
            "day_level_used": int,
            "pay": dict,
            "saved_from_previous": int,
            "saved_breakdown": list,
            "total_available": int,       # entitled + saved_from_previous
            "projection": dict or None,   # For open (current/future) years
            "closed": dict or None,       # For closed (past) years
        }
    """
    start_month = user.vacation_year_start_month or 4

    # Vacation year boundaries
    year_start, year_end = get_vacation_year_boundaries(target_year, start_month)

    # Count vacation in scheduled work days: both week-based and day-level vacation exclude
    # the employee's OFF-shift days. Compute the OFF days for the vacation year when the
    # caller did not supply them, so every entry point counts consistently.
    if off_dates is None:
        off_dates = _scheduled_off_dates(user, year_start, year_end)

    # Earning year is the year before the vacation year
    earning_start, earning_end = get_vacation_year_boundaries(target_year - 1, start_month)

    # Determine entitled days
    full_days = user.vacation_days_per_year or 25
    employment_start = user.employment_start_date
    # If user has an employment transition, Handels accrual starts at transition_date
    if (
        employment_start
        and hasattr(user, "employment_transition")
        and user.employment_transition
        and user.employment_transition.transition_date > employment_start
    ):
        employment_start = user.employment_transition.transition_date

    is_first_year = False
    if employment_start and employment_start > earning_start:
        # Employee started during or after the earning year → pro-rate
        is_first_year = True
        entitled_days = _calculate_prorated_days(employment_start, earning_start, earning_end, full_days)
    else:
        entitled_days = full_days

    transition = getattr(user, "employment_transition", None)

    # Count used days in this vacation year
    used = count_vacation_days_used(
        user_id=user.id,
        year_start=year_start,
        year_end=year_end,
        db=db,
        vacation_json=user.vacation,
        off_dates=off_dates,
    )

    # Calculate vacation pay (semestertillägg) with per-user rates
    from app.core.rates import get_user_rates

    user_rates = get_user_rates(user)
    pay = calculate_vacation_pay(
        user=user,
        entitled_days=entitled_days,
        earning_start=earning_start,
        earning_end=earning_end,
        db=db,
        vacation_rates=user_rates["vacation"],
    )

    # Advance vacation: ICA tops up to the full quota in the first direct-employment year.
    # Advance days = days used beyond entitlement, capped at (full_days - entitled_days).
    # Advance days carry no variable supplement; blend the supplement accordingly.
    if is_first_year and transition and used["total"] > 0:
        max_advance = max(0, full_days - entitled_days)
        advance_used = max(0, min(used["total"] - entitled_days, max_advance))
        if advance_used > 0:
            normal_used = used["total"] - advance_used
            blended = (normal_used * pay["supplement_per_day"] + advance_used * pay["fixed_per_day"]) / used["total"]
            pay = dict(pay)
            pay["supplement_per_day"] = round(blended, 2)
            pay["advance_used"] = advance_used

    # Get saved days from previous closed years
    saved_info = get_saved_days_balance(user, target_year)
    saved_from_previous = saved_info["total_saved"]

    total_available = entitled_days + saved_from_previous
    remaining_total = total_available - used["total"]
    # The closing year's own remaining days; saved days from earlier years are
    # handled separately and must not be swept into a close or payout.
    remaining_own = entitled_days - used["total"]

    # Auto-close past years / show projection for open years
    today = datetime.date.today()
    closed = None
    projection = None
    vacation_saved = user.vacation_saved or {}
    year_key = str(target_year)

    if today > year_end:
        # Year has ended — check if already closed or auto-close
        if year_key in vacation_saved:
            closed = vacation_saved[year_key]
        else:
            closed = close_vacation_year(user, target_year, remaining_own, pay, db)
    else:
        # Year is open — show projection of what will happen at year-end
        monthly_salary = pay.get("monthly_salary", 0)
        supplement_per_day = pay.get("supplement_per_day", 0)
        payout_pct = pay.get("payout_pct", 0.046)
        payout_per_day = round(monthly_salary * payout_pct + supplement_per_day, 2)

        days_to_save = min(max(remaining_own, 0), 5)
        days_to_pay_out = max(remaining_own - 5, 0)

        projection = {
            "days_to_save": days_to_save,
            "days_to_pay_out": days_to_pay_out,
            "payout_per_day": payout_per_day,
            "payout_total": round(days_to_pay_out * payout_per_day, 2),
        }

    return {
        "entitled_days": entitled_days,
        "used_days": used["total"],
        "remaining_days": remaining_total,
        "year_start": year_start,
        "year_end": year_end,
        "earning_year_start": earning_start,
        "earning_year_end": earning_end,
        "is_first_year": is_first_year,
        "week_based_used": used["week_based"],
        "day_level_used": used["day_level"],
        "pay": pay,
        "saved_from_previous": saved_from_previous,
        "saved_breakdown": saved_info["breakdown"],
        "total_available": total_available,
        "projection": projection,
        "closed": closed,
    }


def calculate_vacation_pay(
    user,
    entitled_days: int,
    earning_start: datetime.date,
    earning_end: datetime.date,
    db,
    vacation_rates: dict | None = None,
) -> dict:
    """
    Calculate vacation supplement (semestertillägg) per Handelns tjänstemannaavtal 2025-2027.

    When taking vacation, the employee receives their normal monthly salary PLUS
    a vacation supplement per vacation day:

      0.8% of monthly salary (fast del)
    + 0.5% of ALL variable earnings paid during the earning year (rörlig del)

    Variable components (rörliga lönedelar) include all of:
      Skifttillägg/OB, beredskapsersättning, övertid, etc.

    Note: This is NOT semesterersättning (4.6%) which only applies when
    leaving employment with unused vacation days.

    Args:
        user: User ORM object
        entitled_days: Number of vacation days entitled
        earning_start: Start of earning year
        earning_end: End of earning year
        db: Database session
    """
    from app.core.schedule.summary import summarize_month_for_person
    from app.core.schedule.wages import get_effective_monthly_wage

    # Get current wage (monthly equivalent for HOURLY workers)
    try:
        monthly_salary = get_effective_monthly_wage(db, user.id, 0)
    except Exception:
        monthly_salary = user.wage if hasattr(user, "wage") else 0
    if monthly_salary == 0:
        monthly_salary = user.wage if hasattr(user, "wage") else 0

    # Fixed supplement: 0.8% of monthly salary per vacation day (customizable)
    vac = vacation_rates or {"fixed_pct": 0.008, "variable_pct": 0.005, "payout_pct": 0.046}
    fixed_per_day = round(monthly_salary * vac["fixed_pct"], 2)

    # Sum ALL variable earnings during earning year
    ob_total = 0.0
    ot_total = 0.0
    oncall_total = 0.0

    person_id = user.rotation_person_id
    if person_id and 1 <= person_id <= 10:
        current = earning_start
        while current <= earning_end:
            try:
                summary = summarize_month_for_person(
                    year=current.year,
                    month=current.month,
                    person_id=person_id,
                    session=db,
                    fetch_tax_table=False,
                    wage_user_id=user.id,
                )
                ob_pay_dict = summary.get("ob_pay", {})
                ob_total += sum(ob_pay_dict.values())
                ot_total += summary.get("ot_pay", 0.0)
                oncall_total += summary.get("oncall_pay", 0.0)
            except Exception:
                # Keep going so one bad month does not break the whole vacation
                # calculation, but log it: silently dropping a month understates the
                # variable part of the vacation supplement (0.5% of variable earnings).
                logger.warning(
                    "Vacation pay: failed to summarise %d-%02d for user_id=%s; "
                    "its variable earnings are excluded from the supplement",
                    current.year,
                    current.month,
                    user.id,
                    exc_info=True,
                )

            if current.month == 12:
                current = datetime.date(current.year + 1, 1, 1)
            else:
                current = datetime.date(current.year, current.month + 1, 1)

    # All variable components are included (no exclusion rules for semestertillägg)
    variable_total = ob_total + ot_total + oncall_total

    # Variable supplement: 0.5% of total variable earnings, per vacation day (customizable)
    variable_per_day = round(variable_total * vac["variable_pct"], 2)

    # Total supplement per day
    supplement_per_day = round(fixed_per_day + variable_per_day, 2)

    return {
        "fixed_per_day": fixed_per_day,
        "variable_per_day": variable_per_day,
        "supplement_per_day": supplement_per_day,
        "supplement_total": round(supplement_per_day * entitled_days, 2),
        "variable_total": round(variable_total, 2),
        "ob_total": round(ob_total, 2),
        "ot_total": round(ot_total, 2),
        "oncall_total": round(oncall_total, 2),
        "monthly_salary": monthly_salary,
        "payout_pct": vac["payout_pct"],
    }


def fold_vacation_supplement_into_pay(brutto_pay: float, netto_pay: float, supplement: float) -> tuple[float, float]:
    """Fold a period's vacation supplement (semestertillägg) into its gross/net pay.

    The supplement is taxed like ordinary variable pay, so net is increased by the
    supplement scaled by the period's existing net/gross ratio rather than added
    krona-for-krona. Used so headline gross/net totals (year view's per-month rows,
    personal month view's summary) actually include the supplement instead of only
    showing it as a separate informational figure.
    """
    if not supplement:
        return brutto_pay, netto_pay
    brutto_before = brutto_pay or 0
    netto_before = netto_pay or 0
    new_brutto = brutto_before + supplement
    if brutto_before > 0:
        tax_ratio = netto_before / brutto_before
        new_netto = round(netto_before + supplement * tax_ratio, 0)
    else:
        new_netto = netto_before
    return new_brutto, new_netto
