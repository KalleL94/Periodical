"""Employment transition — calculations for consultant → direct employment.

Handles:
- Automatic calculation of average daily variable pay from the earning year
- Vacation payout per the Swedish vacation act, under either statutory rule
- Pay split for the transition month per employer

Two rules can produce the payout, picked per transition because which one applies
depends on the consultant employer's agreement:

- SAME_PAY ("sammalöneregeln", Semesterlagen 16 a): per unused day, 4.6% of the
  current monthly salary plus a supplement of `consultant_supplement_pct` of that
  salary, plus 0.5% of the variable pay earned in that day's own earning year.
- PERCENTAGE ("procentregeln", Semesterlagen 16 b): 12% of all pay that fell due
  during the earning year, spread over the days that year earned. Semesterlagen 16 §
  makes this the required rule when pay is variable to a substantial degree.
"""

import datetime
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.database.database import EmploymentTransition, User

SAME_PAY_RULE = "sammalone"
PERCENTAGE_RULE = "procent"
VACATION_PAYOUT_RULES = (SAME_PAY_RULE, PERCENTAGE_RULE)
# Reported when an engagement spans earning years configured with different rules.
MIXED_RULES = "mixed"

# Semesterlagen 16 b: 12% of the earning year's pay is the whole vacation pay,
# so it replaces the same-pay rule rather than adding to it.
PERCENTAGE_RULE_PCT = 0.12


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


def get_earning_years(
    user: "User",
    transition: "EmploymentTransition",
    full_year_days: int = 25,
    session=None,
) -> list[dict] | None:
    """
    Every earning year behind the payout, each with its own days and its own pay.

    Vacation is earned and paid per earning year (1 April–31 March), and both the
    variable supplement (0.5% of that year's variable pay) and the percentage rule
    (12% of that year's total pay) draw on the pay that fell due in *that* year.
    Collapsing the years into one window would pay the wrong rate for the older days,
    so every caller that needs money, not just a day count, goes through here.

    Days earned follow the Swedish Vacation Act §7:
        ceil(full_year_days * employed_days / total_days_in_earning_year)
    minus the days already used in the matching vacation year (needs a session).

    If transition.earning_year_start/end are manually set, they are used as a single
    custom earning year (backward-compatible with older configurations).

    Returns:
        One dict per earning year, or None if employment_start_date is missing:
        {
            "start": date,        # earning year start, clipped to employment
            "end": date,          # earning year end, clipped to the transition
            "earned": int,        # days earned in this year
            "used": int,          # days already taken from this year
            "days": int,          # net days still to pay out
            "variable": float,    # OB + on-call + overtime that fell due in the window
            "total_pay": float,   # all gross pay that fell due in the window
        }
        `variable` and `total_pay` are 0.0 without a session.
    """
    if not user.employment_start_date:
        return None

    last_day = transition.transition_date - datetime.timedelta(days=1)

    def _row(start, end, earned, used, vacation_year_start, is_final):
        lag = _payroll_lag_months(user, vacation_year_start)

        # Variable pay is paid the month after it is worked, so the pay that *fell due*
        # inside the earning year is the pay worked in the window shifted back by the
        # payroll lag. The monthly salary does not lag, so its window stays put. This
        # matches the shifted window vacation.py already computes the variable lump on.
        #
        # The final year is not shifted at its end: the engagement stops there, and the
        # last months' variable pay is settled by the consultant employer along with
        # everything else it still owes. Shifting that end would leave it in no earning
        # year at all, even though it is paid out.
        if session:
            variable, _ = _pay_for_window(
                user,
                session,
                _shift_months(start, lag),
                end if is_final else _shift_months(end, lag, to_month_end=True),
            )
            _, base = _pay_for_window(user, session, start, end)
        else:
            variable, base = 0.0, 0.0

        return {
            "start": start,
            "end": end,
            "vacation_year_start": vacation_year_start,
            "earned": earned,
            "used": used,
            "days": max(0, earned - used),
            "variable": variable,
            "total_pay": round(base + variable, 2),
            "lump_settled": _lump_already_paid(user, vacation_year_start, transition.transition_date),
            "rule": _settings_for(user, vacation_year_start)["payout_rule"],
        }

    # Manual override: single custom earning period (legacy / admin-configured)
    if transition.earning_year_start and transition.earning_year_end:
        earning_start = transition.earning_year_start
        earning_end = transition.earning_year_end
        overlap_start = max(user.employment_start_date, earning_start)
        overlap_end = min(last_day, earning_end)
        if overlap_start > overlap_end:
            return []
        employed_days = (overlap_end - overlap_start).days + 1
        total_days = (earning_end - earning_start).days + 1
        earned = math.ceil(full_year_days * employed_days / total_days)
        return [_row(overlap_start, overlap_end, earned, 0, earning_end + datetime.timedelta(days=1), True)]

    # Auto mode: iterate all April–March earning years from employment start to transition
    from app.core.schedule.vacation import count_vacation_days_used

    employment_start = user.employment_start_date
    april_year = employment_start.year if employment_start.month >= 4 else employment_start.year - 1
    current_april = datetime.date(april_year, 4, 1)

    years: list[dict] = []
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

            years.append(_row(overlap_start, period_end, earned, used, next_april, period_end == last_day))

        current_april = next_april

    return years


def calculate_consultant_vacation_days(
    user: "User",
    transition: "EmploymentTransition",
    full_year_days: int = 25,
    session=None,
) -> int | None:
    """
    Net vacation days to pay out at the end of the consultant engagement.

    Sums the per-year days from get_earning_years; see that function for the formula.
    Without a session, gross earned days are returned (used days not deducted).
    """
    years = get_earning_years(user, transition, full_year_days, session)
    if years is None:
        return None
    return sum(year["days"] for year in years)


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


def _shift_months(date: datetime.date, months: int, to_month_end: bool = False) -> datetime.date:
    """The same date `months` months earlier, optionally snapped to that month's last day."""
    import calendar

    total = (date.year * 12 + date.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    days_in_month = calendar.monthrange(year, month)[1]
    day = days_in_month if to_month_end else min(date.day, days_in_month)
    return datetime.date(year, month, day)


def _settings_for(user: "User", vacation_year_start: datetime.date) -> dict:
    """The payout settings in force for the vacation year an earning year feeds."""
    from app.core.schedule.vacation import vacation_settings_for_year

    return vacation_settings_for_year(user, vacation_year_start.year)


def _payroll_lag_months(user: "User", vacation_year_start: datetime.date) -> int:
    """How many months variable pay lags the month it was worked in."""
    lag = _settings_for(user, vacation_year_start)["variable_lump_lag_months"]
    return int(lag) if lag is not None else 1


def _lump_already_paid(
    user: "User",
    vacation_year_start: datetime.date,
    transition_date: datetime.date,
) -> bool:
    """
    Whether this earning year's variable supplement was already settled as a lump.

    An employer paying the variable part as a yearly lump settles the whole earning
    year in one month of the vacation year that follows it. If that month falls before
    the transition, the money is already paid and the payout must not hand it over a
    second time, once per unused day.
    """
    from app.core.schedule.vacation import _lump_payout_month

    settings = _settings_for(user, vacation_year_start)
    if settings["variable_payout"] != "lump":
        return False

    month = _lump_payout_month(settings["variable_payout_month"], user)
    # The lump month belongs to the vacation year, so it rolls into the next calendar
    # year whenever it sits before the month that vacation year starts in.
    year = vacation_year_start.year if month >= vacation_year_start.month else vacation_year_start.year + 1
    return datetime.date(year, month, 1) < transition_date


def _pay_for_window(user: "User", session, start: datetime.date, end: datetime.date) -> tuple[float, float]:
    """
    Variable pay and base pay for the months worked between start and end, inclusive.

    Variable pay is OB supplement + on-call compensation + overtime. Base pay is the
    rest of the month's brutto, i.e. the monthly salary net of absence deductions.
    Callers combine the two across different windows, which is why they come apart
    here rather than as one total.

    Months are summed whole and scaled at the window edges: summarize_month_for_person
    always reports a full monthly salary even for a month the person was not employed
    through, so a partly covered month has to be pro-rated here rather than trusted.

    Returns:
        (variable_pay, base_pay), both 0.0 if the person has no rotation slot.
    """
    import calendar

    from app.core.schedule.summary import summarize_month_for_person

    person_id = user.rotation_person_id
    if not person_id or not (1 <= person_id <= 10):
        return 0.0, 0.0

    variable = 0.0
    base = 0.0
    for year, month in _iter_months(start, end):
        try:
            summary = summarize_month_for_person(
                year=year,
                month=month,
                person_id=person_id,
                session=session,
                fetch_tax_table=False,
                wage_user_id=user.id,
            )
        except Exception:
            continue

        days_in_month = calendar.monthrange(year, month)[1]
        month_start = datetime.date(year, month, 1)
        month_end = datetime.date(year, month, days_in_month)
        covered = (min(end, month_end) - max(start, month_start)).days + 1
        # ponytail: a partial month is pro-rated by calendar days, so its variable pay is
        # spread evenly rather than followed shift by shift. Sum the days themselves if a
        # mid-month employment start ever needs to be exact.
        share = covered / days_in_month

        month_variable = (
            sum(summary.get("ob_pay", {}).values()) + summary.get("ot_pay", 0.0) + summary.get("oncall_pay", 0.0)
        )
        variable += month_variable * share
        base += (summary.get("brutto_pay", 0.0) - month_variable) * share

    return round(variable, 2), round(base, 2)


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

    total_variable, _ = _pay_for_window(user, session, earning_start, earning_end)
    if total_variable == 0.0:
        return None

    return round(total_variable / working_days, 4)


# ---------------------------------------------------------------------------
# Vacation payout (Swedish Vacation Act — same-pay rule and percentage rule)
# ---------------------------------------------------------------------------


def calculate_consultant_vacation_payout(
    transition: "EmploymentTransition",
    user: "User",
    session,
) -> dict:
    """
    Calculates the vacation payout at the end of the consultant engagement.

    Both statutory rules are supported; `transition.vacation_payout_rule` picks one.

    SAME_PAY (Semesterlagen 16 a), per unused day:
        Base:     monthly_salary × (payout_pct + supplement_pct)
        Variable: variable_pct × the variable pay of that day's own earning year
                  (or variable_avg_daily_override × days, when set)

    PERCENTAGE (Semesterlagen 16 b), per earning year:
        12% of all pay that fell due that year, spread over the days that year earned,
        times the days still unused. This is the whole vacation pay, not a supplement,
        so there is no separate base component.

    Args:
        transition: EmploymentTransition object for the user
        user: User object
        session: SQLAlchemy session

    Returns:
        Dict with detailed breakdown:
        {
            "rule": str,                     # SAME_PAY_RULE or PERCENTAGE_RULE
            "vacation_days": int,
            "monthly_salary": int,
            "base_per_day": float,           # SAME_PAY only, 0.0 under PERCENTAGE
            "supplement_pct": float,
            "base_with_supplement_per_day": float,
            "base_payout": float,
            "variable_avg_daily": float | None,   # SAME_PAY only
            "variable_auto_calculated": bool,
            "variable_payout": float,
            "years": list[dict],             # per earning year, see below
            "total": float,
            "earning_year_start": date,
            "earning_year_end": date,
        }
        Each entry in "years" adds "payout" and "per_day" to the get_earning_years dict.
    """
    from app.core.rates import DEFAULT_VACATION_RATES
    from app.core.schedule.wages import get_effective_monthly_wage

    earning_start, earning_end = get_earning_year(transition)
    # Always recalculate the days from history (earned minus already used before the
    # transition) so the payout reflects the actual state at the time of calculation.
    years = get_earning_years(user, transition, session=session) or []
    _apply_day_override(years, transition.consultant_vacation_days)
    days = sum(year["days"] for year in years)
    supplement_pct = transition.consultant_supplement_pct

    # Consultant wage: wage on the day before the transition (from WageHistory or User.wage)
    last_consultant_day = transition.transition_date - datetime.timedelta(days=1)
    monthly_salary = get_effective_monthly_wage(
        session, user.id, fallback=user.wage, effective_date=last_consultant_day
    )

    # Statutory defaults, deliberately not the user's own RateHistory: those rates
    # describe the direct employment's agreement (and may settle the variable part
    # as a yearly lump, i.e. variable_pct = 0), while this payout comes from the
    # consultant employer. Per-transition tuning goes through consultant_supplement_pct
    # and variable_avg_daily_override.
    payout_pct = DEFAULT_VACATION_RATES["payout_pct"]
    variable_pct = DEFAULT_VACATION_RATES["variable_pct"]

    base_per_day = round(monthly_salary * payout_pct, 4)
    base_with_supplement_per_day = round(monthly_salary * (payout_pct + supplement_pct), 4)
    base_payout = 0.0
    variable_payout = 0.0
    same_pay_days = 0
    variable_auto_calculated = transition.variable_avg_daily_override is None
    override = transition.variable_avg_daily_override

    # Each earning year follows the rule its own vacation year is configured with, so
    # an engagement spanning a change of agreement settles each year the way that year
    # was actually run.
    for year in years:
        if year["rule"] == PERCENTAGE_RULE:
            # 12% of the year's pay, spread over the days that year earned. Dividing by
            # `earned` and not `days` is the point: the days already taken drew their
            # share when they were taken, so the unused ones keep only their own share.
            per_day = PERCENTAGE_RULE_PCT * year["total_pay"] / year["earned"] if year["earned"] else 0.0
            year["per_day"] = round(per_day, 2)
            year["payout"] = round(per_day * year["days"], 2)
            base_payout += year["payout"]
            continue

        # 0.5% of each year's own variable pay: an older day is worth what its own
        # earning year paid, not what the most recent one did. A year whose variable
        # supplement was already settled as a lump gets nothing here, or it would be
        # paid twice. An explicit override still wins: it is a stated intent.
        if override is not None:
            var_per_day = override
        elif year["lump_settled"]:
            var_per_day = 0.0
        else:
            var_per_day = variable_pct * year["variable"]

        year["per_day"] = round(base_with_supplement_per_day + var_per_day, 2)
        year["payout"] = round(year["per_day"] * year["days"], 2)
        base_payout += base_with_supplement_per_day * year["days"]
        variable_payout += var_per_day * year["days"]
        same_pay_days += year["days"]

    base_payout = round(base_payout, 2)
    variable_payout = round(variable_payout, 2)
    avg_daily = round(variable_payout / same_pay_days, 4) if same_pay_days else override

    # One rule for the whole payout when the years agree, which they normally do.
    rules = {year["rule"] for year in years}
    rule = rules.pop() if len(rules) == 1 else MIXED_RULES

    if rule == PERCENTAGE_RULE:
        base_per_day = 0.0
        base_with_supplement_per_day = 0.0

    total = round(base_payout + variable_payout, 2)

    return {
        "rule": rule,
        "vacation_days": days,
        "monthly_salary": monthly_salary,
        "base_per_day": base_per_day,
        "payout_pct": payout_pct,
        "variable_pct": variable_pct,
        "supplement_pct": supplement_pct,
        "base_with_supplement_per_day": base_with_supplement_per_day,
        "base_payout": base_payout,
        "variable_avg_daily": avg_daily,
        "variable_auto_calculated": variable_auto_calculated,
        "variable_payout": variable_payout,
        "years": years,
        "total": total,
        "earning_year_start": earning_start,
        "earning_year_end": earning_end,
    }


def _apply_day_override(years: list[dict], stored_days: float | None) -> None:
    """
    Redistribute a manually entered day total over the earning years, in place.

    The form stores the auto-calculated total when the override field is left blank,
    so a stored value that still matches the calculation is not an override and is
    left alone. A real override is filled oldest year first, because vacation days
    are consumed oldest first; anything beyond what the years earned lands on the
    most recent year.
    """
    if not stored_days or not years:
        return
    if stored_days == sum(year["days"] for year in years):
        return

    remaining = stored_days
    for year in years:
        taken = min(remaining, year["earned"])
        year["days"] = taken
        remaining -= taken
    if remaining:
        years[-1]["days"] += remaining


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
