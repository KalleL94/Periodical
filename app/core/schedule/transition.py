"""Employment transition — calculations for consultant → direct employment.

Handles:
- Automatic calculation of average daily variable pay from the earning year
- Vacation payout per the Swedish vacation act (same-pay rule)
- Pay split for the transition month per employer
"""

import datetime
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.database.database import EmploymentTransition, User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_earning_year(
    transition: "EmploymentTransition",
) -> tuple[datetime.date, datetime.date]:
    """
    Räknar ut intjänandeåret för konsultens semester.

    Under semesterlagen löper intjänandeåret 1 april–31 mars.
    Om transition.earning_year_start/end är satta används de istället.

    Returns:
        (earning_start, earning_end) som datetime.date
    """
    if transition.earning_year_start and transition.earning_year_end:
        return transition.earning_year_start, transition.earning_year_end

    end = transition.transition_date - datetime.timedelta(days=1)
    # Most recent April 1st on or before the last consultant day
    april_year = end.year if end.month >= 4 else end.year - 1
    start = datetime.date(april_year, 4, 1)
    return start, end


def calculate_consultant_vacation_days(
    user: "User",
    transition: "EmploymentTransition",
    full_year_days: int = 25,
    session=None,
) -> int | None:
    """
    Calculates net vacation days to pay out at the end of the consultant engagement.

    Formula (Swedish Vacation Act §7):
        ceil(full_year_days * employed_days / total_days_in_earning_year)

    Iterates over ALL earning years (1 April–31 March) from employment_start_date
    to the day before transition_date. Per earning year:
        earned days - days used in the corresponding vacation year (from vacation year
        start up to but not including transition_date)

    If a session is provided, actual used days are fetched from the database.
    Without session, gross earned days are returned (used days not deducted).

    If transition.earning_year_start/end are manually set, they are used as a single
    custom earning year (backward-compatible with older configurations).

    Returns:
        Net days to pay out (ceiling per year), or None if data is missing.
    """
    if not user.employment_start_date:
        return None

    last_day = transition.transition_date - datetime.timedelta(days=1)

    # Manual override: single custom earning period (legacy / admin-configured)
    if transition.earning_year_start and transition.earning_year_end:
        earning_start = transition.earning_year_start
        earning_end = transition.earning_year_end
        overlap_start = max(user.employment_start_date, earning_start)
        overlap_end = min(last_day, earning_end)
        if overlap_start > overlap_end:
            return 0
        employed_days = (overlap_end - overlap_start).days + 1
        total_days = (earning_end - earning_start).days + 1
        return math.ceil(full_year_days * employed_days / total_days)

    # Auto mode: iterate all April–March earning years from employment start to transition
    from app.core.schedule.vacation import count_vacation_days_used

    employment_start = user.employment_start_date
    april_year = employment_start.year if employment_start.month >= 4 else employment_start.year - 1
    current_april = datetime.date(april_year, 4, 1)

    total = 0
    while current_april <= last_day:
        next_april = datetime.date(current_april.year + 1, 4, 1)
        full_year_end = next_april - datetime.timedelta(days=1)  # 31 mars

        period_end = min(full_year_end, last_day)
        overlap_start = max(employment_start, current_april)

        if overlap_start <= period_end:
            employed_days = (period_end - overlap_start).days + 1
            total_days = (full_year_end - current_april).days + 1
            earned = math.ceil(full_year_days * employed_days / total_days)

            # Deduct vacation days used in the corresponding vacation year (earning year + 1 year)
            # up to (but not including) the transition date
            used = 0
            if session and earned > 0:
                vac_year_start = next_april  # Vacation year starts the month after the earning year ends
                vac_year_end = min(last_day, datetime.date(next_april.year + 1, 4, 1) - datetime.timedelta(days=1))
                if vac_year_start <= vac_year_end:
                    used_data = count_vacation_days_used(
                        user_id=user.id,
                        year_start=vac_year_start,
                        year_end=vac_year_end,
                        db=session,
                        vacation_json=user.vacation,
                    )
                    used = used_data["total"]

            total += max(0, earned - used)

        current_april = next_april

    return total


def _iter_months(start: datetime.date, end: datetime.date) -> list[tuple[int, int]]:
    """Returns a list of (year, month) tuples for every month in the range."""
    months = []
    current = datetime.date(start.year, start.month, 1)
    while current <= end:
        months.append((current.year, current.month))
        if current.month == 12:
            current = datetime.date(current.year + 1, 1, 1)
        else:
            current = datetime.date(current.year, current.month + 1, 1)
    return months


# ---------------------------------------------------------------------------
# Variable average pay
# ---------------------------------------------------------------------------


def calculate_variable_avg_daily(
    user: "User",
    session,
    earning_start: datetime.date,
    earning_end: datetime.date,
) -> float | None:
    """
    Calculates the average daily variable pay during the earning year.

    Variable pay = OB supplement + on-call compensation + overtime.
    The denominator is actual working days (shifts N1/N2/N3/OC/OT),
    excluding OFF, SEM, and days before the employment start date.

    Returns:
        Average variable pay per day in SEK, or None if data is missing.
    """
    from app.core.schedule.period import generate_period_data
    from app.core.schedule.summary import summarize_month_for_person

    person_id = user.rotation_person_id
    if not person_id or not (1 <= person_id <= 10):
        return None

    # Count actual working days via period data (OB not needed here — calculated below via summary)
    try:
        all_days = generate_period_data(
            start_date=earning_start,
            end_date=earning_end,
            person_id=person_id,
            session=session,
        )
    except Exception:
        return None

    working_days = 0
    for day in all_days:
        if day.get("before_employment"):
            continue
        shift = day.get("shift")
        shift_code = shift.code if shift else None
        if shift_code in ("OFF", "SEM", None):
            continue
        working_days += 1

    if working_days == 0:
        return None

    # Sum variable pay components per month (same pattern as vacation.py)
    ob_total = 0.0
    ot_total = 0.0
    oncall_total = 0.0

    for year, month in _iter_months(earning_start, earning_end):
        try:
            summary = summarize_month_for_person(
                year=year,
                month=month,
                person_id=person_id,
                session=session,
                fetch_tax_table=False,
                wage_user_id=user.id,
            )
            ob_pay_dict = summary.get("ob_pay", {})
            ob_total += sum(ob_pay_dict.values())
            ot_total += summary.get("ot_pay", 0.0)
            oncall_total += summary.get("oncall_pay", 0.0)
        except Exception:
            pass

    total_variable = ob_total + ot_total + oncall_total
    if total_variable == 0.0:
        return None

    return round(total_variable / working_days, 4)


# ---------------------------------------------------------------------------
# Vacation payout (Swedish Vacation Act — same-pay rule)
# ---------------------------------------------------------------------------


def calculate_consultant_vacation_payout(
    transition: "EmploymentTransition",
    user: "User",
    session,
) -> dict:
    """
    Calculates the vacation payout at the end of the consultant engagement.

    Formula (Swedish Vacation Act, same-pay rule):
        Base component:     (monthly_salary / 21.75) × days × (1 + supplement_pct)
        Variable component: average daily variable pay × days

    Args:
        transition: EmploymentTransition object for the user
        user: User object
        session: SQLAlchemy session

    Returns:
        Dict with detailed breakdown:
        {
            "vacation_days": float,
            "monthly_salary": int,
            "base_per_day": float,
            "supplement_pct": float,
            "base_with_supplement_per_day": float,
            "base_payout": float,
            "variable_avg_daily": float | None,
            "variable_auto_calculated": bool,
            "variable_payout": float,
            "total": float,
            "earning_year_start": date,
            "earning_year_end": date,
        }
    """
    from app.core.schedule.wages import get_effective_monthly_wage

    earning_start, earning_end = get_earning_year(transition)
    # Always dynamically calculate net vacation days (earned minus already used before transition)
    # so the payout reflects the actual state at the time of calculation.
    days = calculate_consultant_vacation_days(user, transition, session=session) or 0
    supplement_pct = transition.consultant_supplement_pct

    # Consultant wage: wage on the day before the transition (from WageHistory or User.wage)
    last_consultant_day = transition.transition_date - datetime.timedelta(days=1)
    monthly_salary = get_effective_monthly_wage(
        session, user.id, fallback=user.wage, effective_date=last_consultant_day
    )

    # Base component: same-pay rule
    base_per_day = monthly_salary / 21.75
    base_with_supplement_per_day = round(base_per_day * (1 + supplement_pct), 4)
    base_payout = round(base_with_supplement_per_day * days, 2)

    # Variable component
    variable_auto_calculated = transition.variable_avg_daily_override is None
    if variable_auto_calculated:
        avg_daily = calculate_variable_avg_daily(user, session, earning_start, earning_end)
    else:
        avg_daily = transition.variable_avg_daily_override

    variable_payout = round((avg_daily or 0.0) * days, 2)
    total = round(base_payout + variable_payout, 2)

    return {
        "vacation_days": days,
        "monthly_salary": monthly_salary,
        "base_per_day": round(base_per_day, 4),
        "supplement_pct": supplement_pct,
        "base_with_supplement_per_day": base_with_supplement_per_day,
        "base_payout": base_payout,
        "variable_avg_daily": avg_daily,
        "variable_auto_calculated": variable_auto_calculated,
        "variable_payout": variable_payout,
        "total": total,
        "earning_year_start": earning_start,
        "earning_year_end": earning_end,
    }


# ---------------------------------------------------------------------------
# Transition month pay breakdown
# ---------------------------------------------------------------------------


def calculate_transition_month_summary(
    transition: "EmploymentTransition",
    user: "User",
    session,
) -> dict:
    """
    Calculates the expected pay for the transition month, split per employer.

    Rules:
    - TRAILING (lagging consultant pay):
        Consultant employer pays: last consultant month's base + vacation payout
        Direct employer pays: accrued base salary for the transition month
    - CURRENT (current consultant pay):
        Consultant employer pays: vacation payout only (no extra base)
        Direct employer pays: accrued base salary for the transition month

    Note: Handels variable components (OB/on-call) for the transition month
    are paid the following month (trailing variable), not included here.

    Returns:
        {
            "transition_year": int,
            "transition_month": int,
            "transition_date": date,
            "consultant_salary_type": str,
            "consultant_employer": {
                "trailing_base": float | None,     # Last consultant month's base (if TRAILING)
                "trailing_variable": float | None, # Last consultant month's variable (OB+OC+OT, if TRAILING)
                "trailing_variable_breakdown": dict | None,  # {ob, oncall, ot}
                "vacation_payout": dict,            # Vacation payout (see calculate_consultant_vacation_payout)
                "total": float,
            },
            "direct_employer": {
                "base_salary": int,                # Accrued base salary for the transition month
                "note_variable": str,              # Explanation for why variable pay is excluded
            },
            "grand_total_gross": float,            # Total gross from both employers
        }
    """
    from app.core.schedule.summary import summarize_month_for_person
    from app.core.schedule.wages import get_effective_monthly_wage
    from app.database.database import ConsultantSalaryType

    t_date = transition.transition_date
    last_consultant_day = t_date - datetime.timedelta(days=1)

    # Consultant wage (wage on the day before the transition)
    consultant_monthly = get_effective_monthly_wage(
        session, user.id, fallback=user.wage, effective_date=last_consultant_day
    )

    # Direct employer wage (wage on/after the transition date)
    direct_monthly = get_effective_monthly_wage(session, user.id, fallback=user.wage, effective_date=t_date)

    # Vacation payout from the consultant employer
    vacation_payout = calculate_consultant_vacation_payout(transition, user, session)

    # Consultant employer may also pay trailing base + variable components
    trailing_base: float | None = None
    trailing_variable: float | None = None
    trailing_variable_breakdown: dict | None = None

    if transition.consultant_salary_type == ConsultantSalaryType.TRAILING:
        trailing_base = float(consultant_monthly)

        # Variable components from the last consultant month
        person_id = user.rotation_person_id
        if person_id and 1 <= person_id <= 10:
            try:
                last_summary = summarize_month_for_person(
                    year=last_consultant_day.year,
                    month=last_consultant_day.month,
                    person_id=person_id,
                    session=session,
                    fetch_tax_table=False,
                    wage_user_id=user.id,
                )
                ob_pay = round(sum(last_summary.get("ob_pay", {}).values()), 2)
                oncall_pay = round(last_summary.get("oncall_pay", 0.0), 2)
                ot_pay = round(last_summary.get("ot_pay", 0.0), 2)
                trailing_variable = round(ob_pay + oncall_pay + ot_pay, 2)
                trailing_variable_breakdown = {
                    "ob": ob_pay,
                    "oncall": oncall_pay,
                    "ot": ot_pay,
                }
            except Exception:
                pass

    consultant_total = round(
        (trailing_base or 0.0) + (trailing_variable or 0.0) + vacation_payout["total"],
        2,
    )

    return {
        "transition_year": t_date.year,
        "transition_month": t_date.month,
        "transition_date": t_date,
        "consultant_salary_type": transition.consultant_salary_type.value,
        "consultant_employer": {
            "trailing_base": trailing_base,
            "trailing_variable": trailing_variable,
            "trailing_variable_breakdown": trailing_variable_breakdown,
            "vacation_payout": vacation_payout,
            "total": consultant_total,
        },
        "direct_employer": {
            "base_salary": direct_monthly,
            "note_variable": (
                "OB och beredskap från övergångsmånaden betalas av ICA månaden efter (släpande rörliga delar)."
            ),
        },
        "grand_total_gross": round(consultant_total + direct_monthly, 2),
    }
