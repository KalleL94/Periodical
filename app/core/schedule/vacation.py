"""Semesterhantering – veckobaserad, dagsnivå och saldoberäkning."""

import datetime
import math

from app.core.constants import PERSON_IDS
from app.core.storage import load_persons


def get_vacation_dates_for_year(year: int) -> dict[int, set[datetime.date]]:
    """
    Hämtar semesterdatum för alla personer för ett år.

    Merges both week-based vacation (User.vacation JSON) and
    day-level vacation (Absence records with type VACATION).

    Returns:
        Dict med person_id -> set av semesterdatum
    """
    from app.database.database import Absence, AbsenceType, SessionLocal, User

    persons = load_persons()
    per_person: dict[int, set[datetime.date]] = {p.id: set() for p in persons}

    db = SessionLocal()
    try:
        users = db.query(User).filter(User.id.in_(PERSON_IDS)).all()

        for user in users:
            vac_by_year = user.vacation or {}
            weeks_for_year = vac_by_year.get(str(year), []) or []

            for week in weeks_for_year:
                for day in range(1, 8):
                    try:
                        d = datetime.date.fromisocalendar(year, week, day)
                        per_person[user.id].add(d)
                    except ValueError:
                        continue

        # Merge day-level vacation from absences table
        day_absences = (
            db.query(Absence)
            .filter(
                Absence.user_id.in_(PERSON_IDS),
                Absence.absence_type == AbsenceType.VACATION,
                Absence.date >= datetime.date(year, 1, 1),
                Absence.date <= datetime.date(year, 12, 31),
            )
            .all()
        )
        for absence in day_absences:
            if absence.user_id in per_person:
                per_person[absence.user_id].add(absence.date)
    finally:
        db.close()

    return per_person


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
) -> int:
    """
    Count weekdays (Mon-Fri) in the given ISO weeks that fall within [period_start, period_end].
    """
    count = 0
    for week in weeks:
        for iso_day in range(1, 6):  # 1=Monday .. 5=Friday
            try:
                d = datetime.date.fromisocalendar(iso_year, week, iso_day)
            except ValueError:
                continue
            if period_start <= d <= period_end:
                count += 1
    return count


def count_vacation_days_used(
    user_id: int,
    year_start: datetime.date,
    year_end: datetime.date,
    db,
    vacation_json: dict | None = None,
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
            week_based += _count_weekdays_in_vacation_weeks(weeks, cal_year, year_start, year_end)

    # Count day-level vacation from absences
    day_level = (
        db.query(Absence)
        .filter(
            Absence.user_id == user_id,
            Absence.absence_type == AbsenceType.VACATION,
            Absence.date >= year_start,
            Absence.date <= year_end,
        )
        .count()
    )

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


def close_vacation_year(user, target_year: int, remaining_total: int, pay: dict, db) -> dict:
    """
    Close a vacation year by saving up to 5 days and calculating payout for the rest.

    Semesterersättning per Handelns avtal §9.5:
      4.6% of monthly salary + 0.8% of monthly salary + 0.5% of variable per day
      = 5.4% of monthly salary + 0.5% of variable per day

    Args:
        user: User ORM object
        target_year: The vacation year to close
        remaining_total: Total remaining days (entitled + saved - used)
        pay: Result dict from calculate_vacation_pay()
        db: Database session

    Returns:
        {"saved": int, "paid_out": int, "payout_amount": float, "payout_per_day": float}
    """
    from sqlalchemy.orm.attributes import flag_modified

    monthly_salary = pay.get("monthly_salary", 0)
    supplement_per_day = pay.get("supplement_per_day", 0)
    payout_pct = pay.get("payout_pct", 0.046)

    # Semesterersättning = payout_pct base + semestertillägg
    payout_per_day = round(monthly_salary * payout_pct + supplement_per_day, 2)

    if remaining_total <= 0:
        days_saved = 0
        days_paid_out = 0
    else:
        days_saved = min(remaining_total, 5)
        days_paid_out = max(remaining_total - 5, 0)

    payout_amount = round(days_paid_out * payout_per_day, 2)

    closed_data = {
        "saved": days_saved,
        "paid_out": days_paid_out,
        "payout_amount": payout_amount,
        "payout_per_day": payout_per_day,
    }

    saved = dict(user.vacation_saved or {})
    saved[str(target_year)] = closed_data
    user.vacation_saved = saved
    flag_modified(user, "vacation_saved")
    db.commit()

    return closed_data


def calculate_vacation_balance(user, target_year: int, db) -> dict:
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

    # Förskottssemester: ICA fyller upp till full kvot det första direktanställningsåret.
    # Antal förskottsdagar = faktiskt använda dagar utöver intjänade, max (full_days - entitled_days).
    # Förskottsdagar ger inget rörligt tillägg, blend:a semestertillägget.
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
            closed = close_vacation_year(user, target_year, remaining_total, pay, db)
    else:
        # Year is open — show projection of what will happen at year-end
        monthly_salary = pay.get("monthly_salary", 0)
        supplement_per_day = pay.get("supplement_per_day", 0)
        payout_pct = pay.get("payout_pct", 0.046)
        payout_per_day = round(monthly_salary * payout_pct + supplement_per_day, 2)

        days_to_save = min(max(remaining_total, 0), 5)
        days_to_pay_out = max(remaining_total - 5, 0)

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
    from app.core.schedule.wages import get_user_wage

    # Get current wage
    try:
        monthly_salary = get_user_wage(db, user.id, 0)
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
                pass

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
