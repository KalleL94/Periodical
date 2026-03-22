"""Anställningsövergång — beräkningar för konsult → direktanställning.

Hanterar:
- Automatisk beräkning av genomsnittlig daglig rörlig lön från intjänandeåret
- Semesterutlösning enligt semesterlagen (sammalöneregeln)
- Uppdelning av övergångsmånadens lön per arbetsgivare
"""

import datetime
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.database.database import EmploymentTransition, User


# ---------------------------------------------------------------------------
# Hjälpfunktioner
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
    # Senaste 1 april som infaller på eller innan sista konsultdagen
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
    Beräknar netto semesterdagar att betala ut vid konsultanställningens slut.

    Formel (semesterlagen §7):
        ceil(full_year_days * anställda_dagar / totala_dagar_i_intjänandeåret)

    Itererar över ALLA intjänandeår (1 april–31 mars) från employment_start_date
    till dagen före transition_date. Per intjänandeår räknas:
        intjänade dagar - använda dagar i motsvarande semesterår (fr.o.m. semesterårets
        start t.o.m. transition_date - 1)

    Om session anges hämtas faktiskt använda dagar från databasen.
    Utan session returneras brutto (använda dagar räknas ej av).

    Om transition.earning_year_start/end är manuellt satta används de som ett
    enda anpassat intjänandeår (bakåtkompatibelt med äldre konfigurationer).

    Returns:
        Netto antal dagar att betala ut (avrundat uppåt per år), eller None om data saknas.
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
                vac_year_start = next_april  # Semesteråret börjar månaden efter intjänandeårets slut
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
    """Returnerar lista av (år, månad) för alla månader i intervallet."""
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
# Rörlig genomsnittslön
# ---------------------------------------------------------------------------


def calculate_variable_avg_daily(
    user: "User",
    session,
    earning_start: datetime.date,
    earning_end: datetime.date,
) -> float | None:
    """
    Beräknar genomsnittlig daglig rörlig lön under intjänandeåret.

    Rörlig lön = OB-tillägg + beredskapsersättning + övertid.
    Nämnaren är faktiska arbetsdagar (skift N1/N2/N3/OC/OT),
    ej OFF-, SEM- eller dagar innan anställningsstart.

    Returns:
        Genomsnittlig rörlig lön per dag i SEK, eller None om data saknas.
    """
    from app.core.schedule.period import generate_period_data
    from app.core.schedule.summary import summarize_month_for_person

    person_id = user.rotation_person_id
    if not person_id or not (1 <= person_id <= 10):
        return None

    # Räkna faktiska arbetsdagar via period-data (ej OB-beräkning — den görs nedan via summary)
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

    # Summera rörliga lönedelar per månad (samma mönster som vacation.py)
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
# Semesterutlösning (semesterlagen — sammalöneregeln)
# ---------------------------------------------------------------------------


def calculate_consultant_vacation_payout(
    transition: "EmploymentTransition",
    user: "User",
    session,
) -> dict:
    """
    Beräknar semesterutlösning vid konsultanställningens slut.

    Formel (semesterlagen, sammalöneregeln):
        Grundkomponent:  (månadslön / 21,75) × dagar × (1 + tilläggsprocent)
        Rörlig komponent: genomsnittlig daglig rörlig lön × dagar

    Args:
        transition: EmploymentTransition-objekt för användaren
        user: User-objekt
        session: SQLAlchemy-session

    Returns:
        Dict med nedbruten beräkning:
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
    from app.core.schedule.wages import get_user_wage

    earning_start, earning_end = get_earning_year(transition)
    # Always dynamically calculate net vacation days (earned minus already used before transition)
    # so the payout reflects the actual state at the time of calculation.
    days = calculate_consultant_vacation_days(user, transition, session=session) or 0
    supplement_pct = transition.consultant_supplement_pct

    # Konsultlön: lönen dagen innan övergången (från WageHistory eller User.wage)
    last_consultant_day = transition.transition_date - datetime.timedelta(days=1)
    monthly_salary = get_user_wage(session, user.id, fallback=user.wage, effective_date=last_consultant_day)

    # Grundkomponent: sammalöneregeln
    base_per_day = monthly_salary / 21.75
    base_with_supplement_per_day = round(base_per_day * (1 + supplement_pct), 4)
    base_payout = round(base_with_supplement_per_day * days, 2)

    # Rörlig komponent
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
# Övergångsmånadens löneuppdelning
# ---------------------------------------------------------------------------


def calculate_transition_month_summary(
    transition: "EmploymentTransition",
    user: "User",
    session,
) -> dict:
    """
    Beräknar förväntad löneutbetalning för övergångsmånaden, uppdelad per arbetsgivare.

    Regler:
    - TRAILING (släpande konsultlön):
        Konsultarbetsgivare betalar: sista konsultmånadens grundlön + semesterutlösning
        Direktarbetsgivare betalar: innestående grundlön för övergångsmånaden
    - CURRENT (innestående konsultlön):
        Konsultarbetsgivare betalar: semesterutlösning (ingen extra grundlön)
        Direktarbetsgivare betalar: innestående grundlön för övergångsmånaden

    Notering: Handels rörliga delar (OB/beredskap) i övergångsmånaden
    betalas ut månaden efter (släpande rörliga), ej inkluderat här.

    Returns:
        {
            "transition_year": int,
            "transition_month": int,
            "transition_date": date,
            "consultant_salary_type": str,
            "consultant_employer": {
                "trailing_base": float | None,     # Sista konsultmånadens grundlön (om TRAILING)
                "trailing_variable": float | None, # Sista konsultmånadens rörliga (OB+OC+OT, om TRAILING)
                "trailing_variable_breakdown": dict | None,  # {ob, oncall, ot}
                "vacation_payout": dict,            # Semesterutlösning (se calculate_consultant_vacation_payout)
                "total": float,
            },
            "direct_employer": {
                "base_salary": int,                # Innestående grundlön övergångsmånaden
                "note_variable": str,              # Förklaring om varför rörliga ej ingår
            },
            "grand_total_gross": float,            # Summa brutto båda arbetsgivarna
        }
    """
    from app.core.schedule.summary import summarize_month_for_person
    from app.core.schedule.wages import get_user_wage
    from app.database.database import ConsultantSalaryType

    t_date = transition.transition_date
    last_consultant_day = t_date - datetime.timedelta(days=1)

    # Konsultlön (lönen dagen innan övergången)
    consultant_monthly = get_user_wage(session, user.id, fallback=user.wage, effective_date=last_consultant_day)

    # Direktlön (lönen på/efter övergångsdatumet)
    direct_monthly = get_user_wage(session, user.id, fallback=user.wage, effective_date=t_date)

    # Semesterutlösning från konsultarbetsgivaren
    vacation_payout = calculate_consultant_vacation_payout(transition, user, session)

    # Konsultarbetsgivaren betalar ev. släpande grundlön + rörliga delar
    trailing_base: float | None = None
    trailing_variable: float | None = None
    trailing_variable_breakdown: dict | None = None

    if transition.consultant_salary_type == ConsultantSalaryType.TRAILING:
        trailing_base = float(consultant_monthly)

        # Rörliga delar från sista konsultmånaden (månaden för last_consultant_day)
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
