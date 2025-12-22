"""Sammanfattningar för månader och år."""

from app.core.storage import load_persons, load_tax_brackets

from .core import get_settings
from .ob import calculate_ob_hours, calculate_ob_pay, get_combined_rules_for_year
from .period import generate_year_data
from .wages import get_absence_deductions_for_month, get_all_user_wages, get_user_wage

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


def _calculate_tax(brutto: float, tax_table: str | None = None) -> float:
    """
    Beräknar skatt baserat på bruttolön.

    Args:
        brutto: Bruttolön i SEK
        tax_table: Skattetabellnummer (t.ex. "33"). Om None används tax_brackets.json

    Returns:
        Skattebelopp i SEK
    """
    from app.core.storage import calculate_tax_bracket, calculate_tax_from_table
    import logging

    logger = logging.getLogger(__name__)

    # Om skattetabell är angiven, använd den
    if tax_table:
        try:
            return calculate_tax_from_table(brutto, tax_table)
        except Exception as e:
            # Fallback till tax_brackets om något går fel
            logger.warning(f"Failed to calculate tax from table {tax_table}: {e}. Using fallback.")

    # Fallback till gamla systemet
    return calculate_tax_bracket(brutto, _get_tax_brackets())


def summarize_year_by_month(year: int, person_id: int) -> dict[int, dict]:
    """
    Grov årsöversikt per månad för en person.

    Returns:
        Dict med månad -> {'total_hours': float, 'num_shifts': int}
    """
    days = generate_year_data(year, person_id)

    summary = {}
    for day in days:
        month = day["date"].month
        shift = day.get("shift")

        if month not in summary:
            summary[month] = {"total_hours": 0.0, "num_shifts": 0}

        summary[month]["total_hours"] += day.get("hours", 0.0)

        if shift and shift.code != "OFF":
            summary[month]["num_shifts"] += 1

    return summary


def summarize_month_for_person(
    year: int,
    month: int,
    person_id: int,
    session=None,
    user_wages: dict[int, int] | None = None,
    year_days: list[dict] | None = None,
) -> dict:
    """
    Detaljerad månadsöversikt för en person.

    Args:
        year: År
        month: Månad (1-12)
        person_id: Person-ID
        session: SQLAlchemy session
        user_wages: Förladdade löner
        year_days: Förgenererad årsdata (optimering)

    Returns:
        Dict med total_hours, num_shifts, ob_hours, ob_pay, brutto/netto, days
    """
    # Använd förgenererad data eller generera ny
    if year_days is None:
        days = generate_year_data(year, person_id, session=session, user_wages=user_wages)
    else:
        days = year_days

    combined_rules = get_combined_rules_for_year(year)
    settings = get_settings()
    persons = _get_persons()

    # Hämta lön och skattetabell från användare
    if user_wages and person_id in user_wages:
        base_salary = user_wages[person_id]
    else:
        try:
            base_salary = get_user_wage(session, person_id, settings.monthly_salary)
        except Exception:
            base_salary = settings.monthly_salary

    # Hämta skattetabell från användare
    tax_table = None
    if session:
        from app.database.database import User
        import logging

        logger = logging.getLogger(__name__)

        user = session.query(User).filter(User.id == person_id).first()
        logger.info(f"Looking up tax_table for person_id={person_id}, user found: {user is not None}")
        if user:
            logger.info(f"User {user.username} has tax_table: {user.tax_table}")
            if user.tax_table:
                tax_table = user.tax_table
        else:
            logger.warning(f"No user found in database for person_id={person_id}")

    # Initiera totaler
    totals = {
        "total_hours": 0.0,
        "num_shifts": 0,
        "ob_hours": {},
        "ob_pay": {},
        "brutto_pay": base_salary,
        "oncall_pay": 0.0,
        "ot_pay": 0.0,
        "absence_deduction": 0.0,
        "absence_hours": 0.0,
        "sick_days": 0,
        "sick_hours": 0.0,
        "vab_days": 0,
        "vab_hours": 0.0,
        "leave_days": 0,
        "leave_hours": 0.0,
    }

    days_out = []

    for day in days:
        if day["date"].month != month:
            continue

        day_data = _process_day_for_summary(day, combined_rules, base_salary, totals)
        days_out.append(day_data)

    # Hämta frånvaroavdrag för månaden
    absence_details = []
    if session:
        absence_info = get_absence_deductions_for_month(session, person_id, year, month, base_salary)
        totals["absence_deduction"] = absence_info["total_deduction"]
        totals["absence_hours"] = absence_info["total_hours"]
        totals["sick_days"] = absence_info["sick_days"]
        totals["sick_hours"] = absence_info["sick_hours"]
        totals["vab_days"] = absence_info["vab_days"]
        totals["vab_hours"] = absence_info["vab_hours"]
        totals["leave_days"] = absence_info["leave_days"]
        totals["leave_hours"] = absence_info["leave_hours"]
        totals["off_days"] = absence_info["off_days"]
        totals["off_hours"] = absence_info["off_hours"]
        absence_details = absence_info["details"]

        # Dra av frånvaroavdrag från bruttolön
        totals["brutto_pay"] -= totals["absence_deduction"]

    # Beräkna netto med användarens skattetabell
    netto_pay = totals["brutto_pay"] - _calculate_tax(totals["brutto_pay"], tax_table)

    return {
        "year": year,
        "month": month,
        "person_id": person_id,
        "person_name": persons[person_id - 1].name,
        "total_hours": totals["total_hours"],
        "num_shifts": totals["num_shifts"],
        "ob_hours": totals["ob_hours"],
        "ob_pay": totals["ob_pay"],
        "oncall_pay": totals["oncall_pay"],
        "ot_pay": totals["ot_pay"],
        "absence_deduction": totals["absence_deduction"],
        "absence_hours": totals["absence_hours"],
        "sick_days": totals["sick_days"],
        "sick_hours": totals.get("sick_hours", 0.0),
        "vab_days": totals["vab_days"],
        "vab_hours": totals.get("vab_hours", 0.0),
        "leave_days": totals["leave_days"],
        "leave_hours": totals.get("leave_hours", 0.0),
        "absence_details": absence_details,
        "brutto_pay": totals["brutto_pay"],
        "netto_pay": netto_pay,
        "days": days_out,
    }


def _process_day_for_summary(
    day: dict,
    combined_rules: list,
    base_salary: int,
    totals: dict,
) -> dict:
    """Processar en dag och uppdaterar totaler."""
    hours = day.get("hours", 0.0)
    shift = day.get("shift")
    start = day.get("start")
    end = day.get("end")

    # Beräkna OB om tillämpligt
    if shift and shift.code not in ("OFF", "OC", "OT") and start and end:
        ob_hours = calculate_ob_hours(start, end, combined_rules)
        ob_pay = calculate_ob_pay(start, end, combined_rules, base_salary)
    else:
        ob_hours = {r.code: 0.0 for r in combined_rules}
        ob_pay = {r.code: 0.0 for r in combined_rules}

    # Uppdatera totaler
    totals["total_hours"] += hours

    if shift and shift.code != "OFF":
        totals["num_shifts"] += 1

    for code, h in ob_hours.items():
        totals["ob_hours"][code] = totals["ob_hours"].get(code, 0.0) + h

    for code, p in ob_pay.items():
        totals["ob_pay"][code] = totals["ob_pay"].get(code, 0.0) + p
        totals["brutto_pay"] += p

    # Lägg till jour och övertid
    oncall_pay = day.get("oncall_pay", 0.0)
    ot_pay = day.get("ot_pay", 0.0)

    totals["brutto_pay"] += oncall_pay + ot_pay
    totals["oncall_pay"] += oncall_pay
    totals["ot_pay"] += ot_pay

    return {
        "date": day["date"],
        "weekday_name": day["weekday_name"],
        "shift": shift,
        "rotation_week": day.get("rotation_week"),
        "hours": hours,
        "ob_hours": ob_hours,
        "ob_pay": ob_pay,
        "oncall_pay": oncall_pay,
        "oncall_details": day.get("oncall_details", {}),
        "ot_pay": ot_pay,
        "ot_hours": day.get("ot_hours", 0.0),
        "ot_details": day.get("ot_details", {}),
        "start": start,
        "end": end,
    }


def summarize_year_for_person(
    year: int,
    person_id: int,
    session=None,
) -> dict:
    """
    Bygger årsöversikt för en person.

    Returns:
        Dict med 'months' (lista med 12 månadsdictar) och 'year_summary'
    """
    # Förladda löner och årsdata EN gång
    user_wages = get_all_user_wages(session)
    year_days = generate_year_data(year, person_id, session=session, user_wages=user_wages)

    months = []
    for month in range(1, 13):
        m = summarize_month_for_person(
            year,
            month,
            person_id,
            session=session,
            user_wages=user_wages,
            year_days=year_days,
        )
        # Beräkna total OB för månaden
        ob_pay = m.get("ob_pay", {}) or {}
        total_ob = sum(float(ob_pay.get(code, 0.0) or 0.0) for code in ("OB1", "OB2", "OB3", "OB4", "OB5"))
        m["total_ob"] = total_ob
        months.append(m)

    year_summary = _build_year_summary(months)

    return {
        "months": months,
        "year_summary": year_summary,
    }


def _build_year_summary(months: list[dict]) -> dict:
    """Bygger årssammanfattning från månadsdata."""
    month_count = len(months) or 1

    # Summera totaler
    total_netto = sum(m.get("netto_pay", 0.0) for m in months)
    total_brutto = sum(m.get("brutto_pay", 0.0) for m in months)
    total_shifts = sum(m.get("num_shifts", 0) for m in months)
    total_hours = sum(m.get("total_hours", 0.0) for m in months)
    total_ob = sum(m.get("total_ob", 0.0) for m in months)
    total_oncall = sum(m.get("oncall_pay", 0.0) for m in months)
    total_ot = sum(m.get("ot_pay", 0.0) for m in months)
    total_absence_deduction = sum(m.get("absence_deduction", 0.0) for m in months)
    total_absence_hours = sum(m.get("absence_hours", 0.0) for m in months)
    total_sick_days = sum(m.get("sick_days", 0) for m in months)
    total_sick_hours = sum(m.get("sick_hours", 0.0) for m in months)
    total_vab_days = sum(m.get("vab_days", 0) for m in months)
    total_vab_hours = sum(m.get("vab_hours", 0.0) for m in months)
    total_leave_days = sum(m.get("leave_days", 0) for m in months)
    total_leave_hours = sum(m.get("leave_hours", 0.0) for m in months)
    total_off_days = sum(m.get("off_days", 0) for m in months)
    total_off_hours = sum(m.get("off_hours", 0.0) for m in months)

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
        "total_ot": total_ot,
        "total_absence_deduction": total_absence_deduction,
        "total_absence_hours": total_absence_hours,
        "total_sick_days": total_sick_days,
        "total_sick_hours": total_sick_hours,
        "total_vab_days": total_vab_days,
        "total_vab_hours": total_vab_hours,
        "total_leave_days": total_leave_days,
        "total_leave_hours": total_leave_hours,
        "total_off_days": total_off_days,
        "total_off_hours": total_off_hours,
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
        "avg_ot": total_ot / month_count,
        "avg_absence_deduction": total_absence_deduction / month_count,
        "ob_hours_by_code": ob_hours_by_code,
        "ob_pay_by_code": ob_pay_by_code,
        "total_ob_hours": sum(ob_hours_by_code.values()),
    }
