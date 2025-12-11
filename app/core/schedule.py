import datetime
from functools import lru_cache


from .storage import (
    load_shift_types,
    load_rotation,
    load_settings,
    load_ob_rules,
    load_tax_brackets,
    calculate_tax_bracket,
    load_persons,
)
from .holidays import *
from .oncall import (
    calculate_oncall_pay, 
    calculate_oncall_pay_for_period, 
    _cached_oncall_rules
)
from .models import ObRule
from .constants import (
    PERSON_IDS,
    WEEKDAY_NAMES,
    VACATION_CODE,
    OB_CODES,
    OB_PRIORITY_BY_CODE,
    OB_PRIORITY_DEFAULT,
)
from .config import (
    DATE_FORMAT_ISO,
    TIME_FORMAT_HM,
    TIME_END_OF_DAY_STRING,
    OB_RATE_DIVISOR_OB4,
    OB_RATE_DIVISOR_OB5,
)
from app.database.database import SessionLocal, User

shift_types = load_shift_types()
rotation = load_rotation()
settings = load_settings()
ob_rules = load_ob_rules()
tax_brackets = load_tax_brackets()
persons = load_persons()

# Rotationens startdatum läses från settings med centralt datumformat.
rotation_start_date: datetime.date = datetime.datetime.strptime(
    settings.rotation_start_date,
    DATE_FORMAT_ISO,
).date()

# Använd centrala veckodagsnamn, men exponera samma namn som tidigare modulvariabel.
weekday_names = list(WEEKDAY_NAMES)

# Månadslön per person, med fallback till global settings-lön.
person_wages = {p.id: getattr(p, "wage", settings.monthly_salary) for p in persons}

# Semester representeras med central VACATION_CODE.
VACATION_SHIFT = next((s for s in shift_types if s.code == VACATION_CODE), None)

def calculate_overtime_pay(monthly_salary: int, hours: float) -> float:
    """
    Calculate overtime pay based on monthly salary and hours worked.

    Formula: (monthly_salary / 72) * hours

    Args:
        monthly_salary: Employee's monthly salary in SEK
        hours: Number of overtime hours worked

    Returns:
        Overtime compensation in SEK

    Example:
        >>> calculate_overtime_pay(30000, 8.5)
        3541.67  # (30000/72) * 8.5
    """
    hourly_rate = monthly_salary / 72
    return hourly_rate * hours

def get_overtime_shift_for_date(session, user_id: int, date: datetime.date):
    """
    Get overtime shift for a specific user and date.

    Args:
        session: SQLAlchemy database session
        user_id: User ID
        date: Date to check

    Returns:
        OvertimeShift object or None
    """
    from app.database.database import OvertimeShift

    return session.query(OvertimeShift).filter(
        OvertimeShift.user_id == user_id,
        OvertimeShift.date == date
    ).first()

def get_overtime_shifts_for_month(session, user_id: int, year: int, month: int):
    """
    Get all overtime shifts for a user in a specific month.

    Args:
        session: SQLAlchemy database session
        user_id: User ID
        year: Year
        month: Month (1-12)

    Returns:
        List of OvertimeShift objects
    """
    from app.database.database import OvertimeShift
    import datetime as dt_module

    start_date = dt_module.date(year, month, 1)
    if month == 12:
        end_date = dt_module.date(year + 1, 1, 1)
    else:
        end_date = dt_module.date(year, month + 1, 1)

    return session.query(OvertimeShift).filter(
        OvertimeShift.user_id == user_id,
        OvertimeShift.date >= start_date,
        OvertimeShift.date < end_date
    ).all()

@lru_cache(maxsize=None)
def determine_shift_for_date(
    date: datetime.date,
    start_week: int = 1,
):
    """
    Returnerar (shift, rotation_week_str) för ett givet datum och personens startvecka.

    - Om datumet ligger före rotation_start_date returneras (None, None)
    - rotation_week beräknas utifrån hur många veckor som gått sedan rotation_start_date
    - start_week förskjuter rotationen per person
    """
    if date < rotation_start_date:
        return None, None

    days_to_first_monday = (7 - rotation_start_date.weekday()) % 7
    if days_to_first_monday == 0 and rotation_start_date.weekday() != 0:
        days_to_first_monday = 7 - rotation_start_date.weekday()

    first_monday = rotation_start_date + datetime.timedelta(days=days_to_first_monday)

    if date < first_monday:
        weeks_passed = 0
    else:
        days_to_first_monday = (date - first_monday).days
        weeks_passed = 1 + (days_to_first_monday // 7)

    rotation_week = str(((weeks_passed + (start_week - 1)) % rotation.rotation_length) + 1)
    
    weekday_index = date.weekday()
    shift_code = rotation.weeks[rotation_week][weekday_index]

    for shift in shift_types:
        if shift.code == shift_code:
            return shift, rotation_week

    return None, None

def build_week_data(
    year: int,
    week: int,
    person_id: int | None = None,
) -> list[dict]:
    """
    Bygger veckodata för ett år/vecka.

    - Om person_id är None: en rad per dag med persons-lista (alla 1–10)
    - Om person_id är satt: en rad per dag med en person
    """
    monday = datetime.date.fromisocalendar(year, week, 1)
    days_in_week = []

    person_ids = [person_id] if person_id is not None else list(PERSON_IDS)

    for offset in range(7):
        current_date = monday + datetime.timedelta(days=offset)
        weekday_index = current_date.weekday()
        weekday_name = weekday_names[weekday_index]
        day_info = {
            "date": current_date,
            "weekday_index": weekday_index,
            "weekday_name": weekday_name
        }

        if person_id is None:
            day_info["persons"] = []
            for pid in person_ids:
                result = determine_shift_for_date(current_date, start_week=pid)
                if result is None:
                    shift = None
                    rotation_week = None
                else:
                    shift, rotation_week = result
                person_data = {
                    "person_id": pid,
                    "person_name": persons[pid - 1].name,
                    "shift": shift,
                    "rotation_week": rotation_week,
                }
                day_info["persons"].append(person_data)
        else:
            result = determine_shift_for_date(current_date, start_week=person_id)
            if result is None:
                shift = None
                rotation_week = None
            else:
                shift, rotation_week = result
            day_info["shift"] = shift
            day_info["rotation_week"] = rotation_week
            day_info["person_id"] = person_id
            day_info["person_name"] = persons[person_id - 1].name

        days_in_week.append(day_info)

    return days_in_week

def generate_year_data(
    year: int,
    person_id: int | None = None,
    session=None,
) -> list[dict]:
    """
    Genererar dagsdata för ett helt år.

    - Om person_id är None: days_in_year med persons-listor
    - Om person_id är satt: varje dag innehåller shift, timmar, start, slut och ev. OB
    """
    special_ob_rules = _cached_special_rules(year)
    combined_ob_rules = ob_rules + special_ob_rules
    start_date = datetime.date(year, 1, 1)
    end_date = datetime.date(year, 12, 31)
    vacation_dates = _vacation_dates_for_year(year)
    days_in_year: list[dict] = []
    current_day = start_date
    if start_date < rotation_start_date:
        current_day = rotation_start_date
    person_ids = [person_id] if person_id is not None else list(PERSON_IDS)

    while current_day <= end_date:
        weekday_index = current_day.weekday()
        weekday_name = weekday_names[weekday_index]
        day_info = {
            "date": current_day,
            "weekday_index": weekday_index,
            "weekday_name": weekday_name
        }
        if person_id is None:
            day_info["persons"] = []
            
            for pid in person_ids:
                
                if (
                    VACATION_SHIFT is not None
                    and current_day in vacation_dates.get(pid, set())
                ):
                    result = determine_shift_for_date(current_day, pid)
                    shift, rotation_week = result
                    shift = VACATION_SHIFT
                    rotation_week = rotation_week
                    hours = 0.0
                    start = None
                    end = None
                else:
                    result = determine_shift_for_date(current_day, pid)
                    if result is None:
                        shift = None
                        rotation_week = None
                        hours = 0.0
                        start = None
                        end = None
                    else:
                        shift, rotation_week = result
                        hours, start, end = _cached_shift_hours(current_day, shift.code)

                # Check for overtime shift - if exists, show OT instead of scheduled shift
                if session:
                    ot_shift = get_overtime_shift_for_date(session, pid, current_day)
                    if ot_shift:
                        # Replace with OT shift
                        ot_shift_type = next((s for s in shift_types if s.code == "OT"), None)
                        if ot_shift_type:
                            shift = ot_shift_type
                            hours = ot_shift.hours
                            # Parse start/end times from OT shift
                            ot_start_str = str(ot_shift.start_time)
                            ot_end_str = str(ot_shift.end_time)
                            if len(ot_start_str.split(":")) == 2:
                                ot_start_str += ":00"
                            if len(ot_end_str.split(":")) == 2:
                                ot_end_str += ":00"
                            try:
                                start_time_obj = datetime.datetime.strptime(ot_start_str, "%H:%M:%S").time()
                                end_time_obj = datetime.datetime.strptime(ot_end_str, "%H:%M:%S").time()
                                start = datetime.datetime.combine(current_day, start_time_obj)
                                end = datetime.datetime.combine(current_day, end_time_obj)
                                if end <= start:
                                    end = end + datetime.timedelta(days=1)
                            except:
                                pass

                person_data = {
                    "person_id": pid,
                    "person_name": persons[pid - 1].name,
                    "shift": shift,
                    "rotation_week": rotation_week,
                    "hours": hours,
                    "start": start ,
                    "end": end,
                }
                day_info["persons"].append(person_data)
        else:
            if (
                VACATION_SHIFT is not None
                and current_day in vacation_dates.get(person_id, set())
            ):
                shift = VACATION_SHIFT
                rotation_week = None
                hours = 0.0
                start = None
                end = None
                ob = {}
            else:
                result = determine_shift_for_date(current_day, person_id)
                if result is None:
                    shift = None
                    rotation_week = None
                    hours = 0.0
                    start = None
                    end = None
                    ob = {}
                else:
                    shift, rotation_week = result
                    hours, start, end = _cached_shift_hours(current_day, shift.code)
                    if start is not None and shift.code != "OC":
                        ob = calculate_ob_hours(start, end, combined_ob_rules)
                    else:
                        ob = {}

            # Callculate on-call pay if this is an on-call shift
            oncall_pay = 0.0
            oncall_details = {}
            if shift and shift.code == "OC":
                oncall_rules = _cached_oncall_rules(current_day.year)
                oncall_calc = calculate_oncall_pay(
                    current_day,
                    person_wages.get(person_id, settings.monthly_salary),
                    oncall_rules
                )
                oncall_pay = oncall_calc["total_pay"]
                oncall_details = oncall_calc
            
            # Check for overtime shift on this date
            ot_shift = get_overtime_shift_for_date(session, person_id, current_day) if session else None
            ot_pay = 0.0
            ot_hours = 0.0
            ot_details = {}

            if ot_shift:
                # If this is an OC shift, recalculate OC pay for shortened period
                if shift and shift.code == "OC":
                    from datetime import time as dt_time

                    # OC shift runs 06:00 to 06:00
                    oc_start = datetime.datetime.combine(current_day, dt_time(6, 0))

                    # OT starts at ot_shift.start_time
                    # Handle both time object and string representation
                    ot_start_str = str(ot_shift.start_time)
                    if len(ot_start_str.split(":")) == 2:
                        ot_start_str += ":00"
                    
                    try:
                        ot_start_time_obj = datetime.datetime.strptime(ot_start_str, "%H:%M:%S").time()
                    except ValueError:
                         # Fallback if format is weird or already time object
                         if isinstance(ot_shift.start_time, datetime.time):
                             ot_start_time_obj = ot_shift.start_time
                         else:
                             # Default fallback, should rarely happen if DB is correct
                             ot_start_time_obj = dt_time(0,0)
                             
                    oc_end = datetime.datetime.combine(current_day, ot_start_time_obj)

                    # Recalculate OC pay for shortened period (06:00 to OT start)
                    if oc_end > oc_start:
                        oncall_rules = _cached_oncall_rules(current_day.year)
                        oncall_calc = calculate_oncall_pay_for_period(
                            oc_start,
                            oc_end,
                            person_wages.get(person_id, settings.monthly_salary),
                            oncall_rules
                        )
                        oncall_pay = oncall_calc["total_pay"]
                        oncall_details = oncall_calc

                # Calculate OT pay
                ot_pay = ot_shift.ot_pay
                ot_hours = ot_shift.hours
                ot_details = {
                    "start_time": str(ot_shift.start_time),
                    "end_time": str(ot_shift.end_time),
                    "hours": ot_hours,
                    "pay": ot_pay,
                    "hourly_rate": person_wages.get(person_id, settings.monthly_salary) / 72
                }

            day_info["person_id"] = person_id
            day_info["person_name"] = persons[person_id - 1].name
            day_info["shift"] = shift
            day_info["rotation_week"] = rotation_week
            day_info["hours"] = hours
            day_info["start"] = start
            day_info["end"] = end
            day_info["ob"] = ob
            day_info["oncall_pay"] = oncall_pay
            day_info["oncall_details"] = oncall_details
            day_info["ot_pay"] = ot_pay
            day_info["ot_hours"] = ot_hours
            day_info["ot_details"] = ot_details

        days_in_year.append(day_info)
        current_day = current_day + datetime.timedelta(days=1)
    return days_in_year

def _select_ob_rules_for_date(
    current_start: datetime.datetime,
    ob_rules: list,
) -> list:
    """
    Väljer vilka OB-regler som gäller för ett visst datum.

    - Matchar på weekday via rule.days
    - Matchar på specifika datum via rule.specific_dates (ISO-strängar)
    - Om OB5 finns denna dag filtreras allt utom OB5 bort
    - Annars om OB4 finns filtreras allt utom OB4 bort
    """
    weekday = current_start.weekday()
    date_iso = current_start.date().isoformat()

    todays_rules = []
    for rule in ob_rules:
        match = False

        # Matcha på veckodag
        if getattr(rule, "days", None):
            if weekday in rule.days:
                match = True

        # Matcha på specifikt datum
        if not match and getattr(rule, "specific_date", None):
            try:
                if date_iso == rule.specific_date:
                    match = True
            except TypeError:
                match = False

        if not match and getattr(rule, "specific_dates", None):
            if date_iso in rule.specific_dates:
                match = True

        if match:
            todays_rules.append(rule)

    return todays_rules

def _ob_rule_priority(r) -> int:
    """
    Prioritet för att fördela tid mellan överlappande regler.

    Själva prioritetsordningen (vilken kod som är "viktigare") ligger
    i OB_PRIORITY_BY_CODE i constants, så att vi slipper hårdkoda
    både koder och siffror här.
    """
    return OB_PRIORITY_BY_CODE.get(r.code, OB_PRIORITY_DEFAULT)

def _vacation_dates_for_year(year: int) -> dict[int, set[datetime.date]]:
    per_person: dict[int, set[datetime.date]] = {p.id: set() for p in persons}
    
    db = SessionLocal()

    try:
        users = db.query(User).filter(User.id.in_(PERSON_IDS)).all()
    
        for user in users:
            vac_by_year = user.vacation or {}
            weeks_for_year = vac_by_year.get(str(year), []) or []

            if not weeks_for_year:
                continue
        
            for week in weeks_for_year:
                for day in range(1, 8):
                    try:
                        d = datetime.date.fromisocalendar(year, week, day)
                    except:
                        # If week is invalid for that year, ignore it instead of crashing
                        continue
                    per_person[user.id].add(d)
    finally:
        db.close()
                
    return per_person

def clear_schedule_cache():
    """Rensar alla cachade schemaberäkningar."""
    generate_year_data.cache_clear()
    _cached_special_rules.cache_clear()
    _cached_shift_hours.cache_clear()
    determine_shift_for_date.cache_clear()

def _rule_interval_for_day(
    rule,
    current_start: datetime.datetime,
) -> tuple[datetime.datetime, datetime.datetime]:
    """
    Bygger (start, end)-intervall för OB-regeln samma kalenderdag som current_start.
    Hanterar "24:00" som midnatt följande dag.
    """
    start_h, start_m = map(int, rule.start_time.split(":"))
    end_h, end_m = map(int, rule.end_time.split(":"))

    ob_start = datetime.datetime(
        current_start.year,
        current_start.month,
        current_start.day,
        start_h,
        start_m
    )

    if rule.end_time == "24:00":
        ob_end = datetime.datetime(
            current_start.year,
            current_start.month,
            current_start.day,
            0,
            0,
        ) + datetime.timedelta(days=1)
    else:
        ob_end = datetime.datetime(
            current_start.year,
            current_start.month,
            current_start.day,
            end_h,
            end_m
        )
    return ob_start, ob_end

def _subtract_covered_interval(
    overlap_start: datetime.datetime,
    overlap_end: datetime.datetime,
    covered: list[tuple[datetime.datetime, datetime.datetime]],
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """
    Tar ett överlapp (overlap_start, overlap_end) och drar bort alla redan
    täckta intervall i 'covered'. Returnerar lista av "otäckta" intervall.
    """
    to_process = [(overlap_start, overlap_end)]
    new_intervals: list[tuple[datetime.datetime, datetime.datetime]] = []

    for seg_start, seg_end in to_process:
        cursor = seg_start
        for cov_start, cov_end in covered:
            if cov_end <= cursor or cov_start >= seg_end:
                continue

            if cov_start > cursor:
                new_intervals.append((cursor, min(cov_start, seg_end)))

            cursor = max(cursor, cov_end)
            if cursor >= seg_end:
                break

        if cursor < seg_end:
            new_intervals.append((cursor, seg_end))

    return new_intervals

def calculate_shift_hours(
    date: datetime.date,
    shift,
) -> tuple[float, datetime.datetime | None, datetime.datetime | None]:
    """
    Beräknar arbetstid i timmar + start/slut-datetime för ett givet datum och shift.
    Hanterar pass som går över midnatt (då flyttas end_datetime till nästa dag).
    """
    if shift is None or shift.code == "OFF":
        return 0.0, None, None
    
    start_time_dt = datetime.datetime.strptime(shift.start_time,"%H:%M")
    end_time_dt = datetime.datetime.strptime(shift.end_time,"%H:%M")

    start_t = start_time_dt.time()
    end_t = end_time_dt.time()

    start_datetime =  datetime.datetime.combine(date, start_t)
    end_datetime =  datetime.datetime.combine(date, end_t)

    if end_t <= start_t:
        end_datetime += datetime.timedelta(days=1)

    delta = end_datetime - start_datetime

    hours = delta.total_seconds() / 3600.0


    return hours, start_datetime, end_datetime

@lru_cache(maxsize=None)
def _cached_shift_hours(date: datetime.date, shift_code: str):
    shift = next((s for s in shift_types if s.code == shift_code), None)
    return calculate_shift_hours(date, shift)

def summarize_year_by_month(
    year: int,
    person_id: int,
) -> dict[int, dict]:
    """
    Grov årsöversikt per månad för en person.
    Returnerar {månad: {'total_hours': float, 'num_shifts': int}}
    """
    days = generate_year_data(year, person_id)

    summary = {}
    for day in days:
        d = day["date"]
        month = d.month
        shift = day.get("shift")
        if month not in summary:
            summary[month] = {
                "total_hours": 0.0,
                "num_shifts": 0
            }
        summary[month]["total_hours"] += day["hours"]
        if shift and shift.code != "OFF":
            summary[month]["num_shifts"] += 1

    return summary

def summarize_month_for_person(
    year: int,
    month: int,
    person_id: int,
    session=None,
) -> dict:
    """
    Detaljerad månadsöversikt för en person.
    Returnerar:
    - total_hours, num_shifts
    - ob_hours och ob_pay per OB-kod
    - brutto/netto-lön
    - days: lista med per-dag-detaljer
    """
    days = generate_year_data(year, person_id, session=session)
    special_rules = _cached_special_rules(year)
    combined_rules = ob_rules + special_rules

    try:
        # monthly_salary = person_wages.get(person_id, settings.monthly_salary)
        person = next(p for p in persons if p.id == person_id)
        base_salary = person.wage
    except StopIteration:
        base_salary = settings.monthly_salary

    totals = {
        "total_hours": 0.0,
        "num_shifts": 0,
        "ob_hours": {},
        "ob_pay": {},
        "brutto_pay": base_salary,
        "oncall_pay": 0.0,
        "ot_pay": 0.0,
    }
    days_out: list[dict] = []
    for day in days:
        if day["date"].month != month:
            continue
        hours = day.get("hours", 0.0)
        shift = day.get("shift")
        start = day.get("start")
        end = day.get("end")
        
        ob_hours: dict[str, float]
        ob_pay: dict[str, float]

        if shift and shift.code != "OFF" and shift.code != "OC" and start and end:
            ob_hours = calculate_ob_hours(start, end, combined_rules)
            ob_pay = calculate_ob_pay(start, end, combined_rules, base_salary)
        else:
            ob_hours = {r.code: 0.0 for r in combined_rules}
            ob_pay = {r.code: 0.0 for r in combined_rules}

        totals["total_hours"] += hours
        if shift and not shift.code == "OFF":
            totals["num_shifts"] += 1

        for code, h in ob_hours.items():
            totals["ob_hours"][code] = totals["ob_hours"].get(code, 0.0) + h
        for code, p in ob_pay.items():
            totals["ob_pay"][code] = totals["ob_pay"].get(code, 0.0) + p
            totals["brutto_pay"] += p

        # Add on-call pay if present
        if 'oncall_pay' in day:
            oncall_pay = day.get('oncall_pay', 0.0)
            totals['brutto_pay'] += oncall_pay
            totals['oncall_pay'] = totals.get('oncall_pay', 0.0) + oncall_pay

        # Add OT pay if present
        if 'ot_pay' in day:
            ot_pay = day.get('ot_pay', 0.0)
            totals['brutto_pay'] += ot_pay
            totals['ot_pay'] = totals.get('ot_pay', 0.0) + ot_pay

        days_out.append({
            "date": day["date"],
            "weekday_name": day["weekday_name"],
            "shift": shift,
            "rotation_week": day.get("rotation_week"),
            "hours": hours,
            "ob_hours": ob_hours,
            "ob_pay": ob_pay,
            "oncall_pay": day.get('oncall_pay', 0.0),
            "oncall_details": day.get('oncall_details', {}),
            "ot_pay": day.get('ot_pay', 0.0),
            "ot_hours": day.get('ot_hours', 0.0),
            "ot_details": day.get('ot_details', {}),
            "start": start,
            "end": end,
        })

    netto_pay = totals["brutto_pay"] - calculate_tax_bracket(
        totals["brutto_pay"],
        tax_brackets
    )
    return {
        'year': year,
        'month': month,
        'person_id': person_id,
        'person_name': persons[person_id - 1].name,
        'total_hours': totals['total_hours'],
        'num_shifts': totals['num_shifts'],
        'ob_hours': totals['ob_hours'],
        'ob_pay': totals['ob_pay'],
        'oncall_pay': totals['oncall_pay'],
        'ot_pay': totals['ot_pay'],
        'brutto_pay': totals['brutto_pay'],
        'netto_pay': netto_pay,
        'days': days_out
    }

def summarize_year_for_person(
    year: int,
    person_id: int,
    session=None,
) -> dict:
    """
    Bygger årsöversikt för en person.

    Returnerar:
      - months: lista med 12 månadsdictar (summarize_month_for_person)
        med extra fält 'total_ob'
      - year_summary: totals och snitt för hela året,
        inklusive OB per kod och totalt.
    """
    months: list[dict] = []

    # Bygg månadslistan och räkna total OB per månad
    for month in range(1, 13):
        m = summarize_month_for_person(year, month, person_id, session=session)
        ob_pay: dict[str, float] = m.get("ob_pay", {}) or {}

        total_ob = 0.0
        for code in ("OB1", "OB2", "OB3", "OB4", "OB5"):
            total_ob += float(ob_pay.get(code, 0.0) or 0.0)

        m["total_ob"] = total_ob
        months.append(m)
    
    month_count = len(months) or 1

    total_netto = sum(m.get("netto_pay", 0.0) for m in months)
    total_brutto = sum(m.get("brutto_pay", 0.0) for m in months)
    total_shifts = sum(m.get("num_shifts", 0) for m in months)
    total_hours = sum(m.get("total_hours", 0.0) for m in months)
    total_ob_year = sum(m.get("total_ob", 0.0) for m in months)
    total_oncall_year = sum(m.get('oncall_pay', 0.0) for m in months)
    total_ot_year = sum(m.get('ot_pay', 0.0) for m in months)

    # OB summering per kod för hela året
    ob_codes = ["OB1", "OB2", "OB3", "OB4", "OB5"]
    ob_hours_by_code: dict[str, float] = {code: 0.0 for code in ob_codes}
    ob_pay_by_code: dict[str, float] = {code: 0.0 for code in ob_codes}
    
    for m in months:
        m_ob_hours: dict[str, float] = m.get("ob_hours", {}) or {}
        m_ob_pay: dict[str, float] = m.get("ob_pay", {}) or {}
        for code in ob_codes:
            ob_hours_by_code[code] += float(m_ob_hours.get(code, 0.0) or 0.0)
            ob_pay_by_code[code] += float(m_ob_pay.get(code, 0.0) or 0.0)
            
    total_ob_hours_year = sum(ob_hours_by_code.values())

    year_summary = {
        "total_netto": total_netto,
        "total_brutto": total_brutto,
        "total_shifts": total_shifts,
        "total_hours": total_hours,
        "total_ob": total_ob_year,
        "total_oncall": total_oncall_year,
        "total_ot": total_ot_year,
        "avg_netto": total_netto / month_count,
        "avg_brutto": total_brutto / month_count,
        "avg_shifts": total_shifts / month_count,
        "avg_hours": total_hours / month_count,
        "avg_ob": total_ob_year / month_count,
        "avg_oncall": total_oncall_year / month_count,
        "avg_ot": total_ot_year / month_count,
        "ob_hours_by_code": ob_hours_by_code,
        "ob_pay_by_code": ob_pay_by_code,
        "total_ob_hours": total_ob_hours_year,
    }

    return {
        "months": months,
        "year_summary": year_summary,
    }



def calculate_ob_hours(
    start_dt: datetime.datetime,
    end_dt: datetime.datetime,
    ob_rules: list,
) -> dict[str, float]:
    """
    Räknar OB-timmar per OB-kod mellan start_dt och end_dt.
    - Går dag för dag
    - Väljer OB-regler som gäller för aktuell dag
    - Prioritet: OB5 > OB4 > alla andra
    - Ser till att samma tid inte räknas dubbelt genom 'covered' intervall
    """
    ob_totals: dict[str, float] = {}
    for rule in ob_rules:
        if rule.code not in ob_totals:
            ob_totals[rule.code] = 0.0

    # Inget pass eller konstigt intervall
    if start_dt is None or end_dt is None:
        return ob_totals
    if end_dt <= start_dt:
        return ob_totals

    current_start = start_dt

    while current_start < end_dt:
        # Avgränsa till slutet av dagen
        next_day = current_start.date() + datetime.timedelta(days=1)
        day_end = datetime.datetime.combine(next_day, datetime.time(0, 0))
        segment_end = end_dt if end_dt <= day_end else day_end

        # Välj regler som gäller idag (med OB5/OB4 preferens)
        todays_rules = _select_ob_rules_for_date(current_start, ob_rules)

        # Sortera efter prioritet så högsta får ta tid först
        rules_by_priority = sorted(
            todays_rules,
            key=_ob_rule_priority,
            reverse=True,
        )

        # Håller koll på redan täckta intervall inom dagens segment
        covered: list[tuple[datetime.datetime, datetime.datetime]] = []

        for rule in rules_by_priority:
            ob_start, ob_end = _rule_interval_for_day(rule, current_start)
            if ob_start >= segment_end:
                continue

            overlap_start = max(current_start, ob_start)
            overlap_end = min(segment_end, ob_end)
            
            if overlap_end <= overlap_start:
                continue

            # Dra bort redan täckta intervall
            new_intervals = _subtract_covered_interval(
                overlap_start,
                overlap_end,
                covered,
            )

            # Lägg till otäckta bitar på rätt OB-kod och markera som täckta
            for ustart, uend in new_intervals:
                hours = (uend - ustart).total_seconds() / 3600.0
                ob_totals[rule.code] = ob_totals.get(rule.code, 0.0) + hours
                covered.append((ustart, uend))

        current_start = segment_end

    return ob_totals

@lru_cache(maxsize=None)
def _cached_ob_hours(start_ts: float, end_ts: float, year: int):
    start_dt = datetime.datetime.fromtimestamp(start_ts)
    end_dt = datetime.datetime.fromtimestamp(end_ts)
    rules = ob_rules + _cached_special_rules(year)
    return calculate_ob_hours(start_dt, end_dt, rules)

def calculate_ob_pay(
    start_dt: datetime.datetime,
    end_dt: datetime.datetime,
    ob_rules: list,
    monthly_salary: int,
) -> dict[str, float]:
    """
    Beräknar OB-lön per OB-kod givet:
    - start/slut på pass
    - lista av OB-regler
    - månadslön
    Timlönen för en regel är monthly_salary / rule.rate.
    """
    hours = calculate_ob_hours(start_dt, end_dt, ob_rules)
    pays = {}
    for rule in ob_rules:
        code = rule.code
        # skip rules with zero hours or missing rate
        h = hours.get(code, 0.0)
        try:
            rate_divisor = getattr(rule, 'rate', None)
            if rate_divisor and h > 0:
                hourly = monthly_salary / float(rate_divisor)
                pays[code] = h * hourly
            else:
                pays[code] = 0.0
        except Exception:
            pays[code] = 0.0
    return pays

def build_special_ob_rules_for_year(year: int) -> list[ObRule]:
    """
    Bygger OB4/OB5-regler för ett år baserat på helgdagar.

    - OB4: “Storhelg 300” på vissa röda dagar (trettondagen, 1 maj, etc)
    - OB5: “Storhelg 150” på långfredag, julafton, nyårsafton m.fl.
    """
    rules: list[ObRule] = []

    def add_interval(
        code: str,
        label: str,
        start_date: datetime.date,
        start_time: str,
        rate: int,
    ) -> None:
        """
        Lägg till en OB regel som gäller från start_date kl start_time
        till kl 00 första vardagen efter den helgen.

        Första dagen använder start_time, följande dagar 00:00 24:00.
        """
        end_first_weekday = first_weekday_after(start_date)
        day = start_date
        first = True
        while day < end_first_weekday:
            st = start_time if first else "00:00"
            et = "24:00"
            rules.append(
                ObRule(
                    code=code,
                    label=label,
                    specific_dates=[day.isoformat()],
                    start_time=st,
                    end_time=et,
                    rate=rate,
                )
            )
            first = False
            day += datetime.timedelta(days=1)

    # OB4 300 enligt avtalet
    add_interval("OB4", "Helgdag 300", trettondagen(year), "07:00", 300)
    add_interval("OB4", "Helgdag 300", forsta_maj(year), "07:00", 300)
    add_interval("OB4", "Helgdag 300", nationaldagen(year), "07:00", 300)
    add_interval("OB4", "Helgdag 300", kristi_himmelsfardsdag(year), "07:00", 300)
    add_interval("OB4", "Helgdag 300", alla_helgons_dag(year), "07:00", 300)
    
    # OB5 150
    # Skärtorsdag från 18 till första vardagen efter påsken
    add_interval("OB5", "Storhelg 150", skartorsdagen(year), "18:00", 150)
    
    # Nyårshelgen: från 18 på nyårsafton året innan,
    # till kl 00 första vardagen efter nyårsdagen i detta år
    ny_prev = nyarsafton(year - 1)
    end_first_weekday = first_weekday_after(nyarsdagen(year))
    day = ny_prev
    first = True
    while day < end_first_weekday:
        st = "18:00" if first else "00:00"
        rules.append(
            ObRule(
                code="OB5",
                label="Storhelg 150",
                specific_dates=[day.isoformat()],
                start_time=st,
                end_time="24:00",
                rate=150,
            )
        )
        first = False
        day += datetime.timedelta(days=1)
        
    # Nyårsafton i det aktuella året
    # (startar 18:00 och fortsätter till första vardagen efter nyårsdagen nästa år)
    ny_this = nyarsafton(year)
    end_first_weekday_next_year = first_weekday_after(nyarsdagen(year + 1))

    day = ny_this
    first = True
    while day < end_first_weekday_next_year:
        st = "18:00" if first else "00:00"
        rules.append(
            ObRule(
                code="OB5",
                label="Storhelg 150",
                specific_dates=[day.isoformat()],
                start_time=st,
                end_time="24:00",
                rate=150,
            )
        )
        first = False
        day += datetime.timedelta(days=1)

    # Långfredagen med efterföljande helg
    add_interval("OB5", "Storhelg 150", langfredagen(year), "00:00", 150)

    # Annandag påsk och helgen fram till första vardagen
    add_interval("OB5", "Storhelg 150", annandagpask(year), "00:00", 150)
    
    def add_eve_block_with_weekend(
        eve_date: datetime.date,
        last_holiday_date: datetime.date,
    ):
        """
        OB5 från 07.00 på aftonen till kl 00 första vardagen
        efter hela helgblocket.

        Exempel jul:
        eve_date = julafton (24/12)
        last_holiday_date = 26/12
        -> OB5 på 24 (från 07), 25, 26, 27 till 00.00 måndag.
        """
        end_first_weekday = first_weekday_after(last_holiday_date)
        day = eve_date
        first = True
        while day < end_first_weekday:
            st = "07:00" if first else "00:00"
            rules.append(
                ObRule(
                    code="OB5",
                    label="Storhelg 150",
                    specific_dates=[day.isoformat()],
                    start_time=st,
                    end_time="24:00",
                    rate=150,
                )
            )
            first = False
            day += datetime.timedelta(days=1)
            
    pingst_eve = pingstafton(year)
    add_eve_block_with_weekend(
        pingst_eve,
        pingst_eve + datetime.timedelta(days=1),
    )
    
    midsummer_eve = midsommarafton(year)
    add_eve_block_with_weekend(
        midsummer_eve,
        midsummer_eve + datetime.timedelta(days=1),
    )
    
    christmas_eve = julafton(year)
    add_eve_block_with_weekend(
        christmas_eve,
        christmas_eve + datetime.timedelta(days=2),
    )

    return rules

@lru_cache(maxsize=10)
def _cached_special_rules(year: int):
    return build_special_ob_rules_for_year(year)

def build_cowork_stats(year: int, target_person_id: int):
    """
    Räknar hur många pass target_person_id jobbar tillsammans
    med varje annan person (1-10), totalt och uppdelat på N1/N2/N3.

    En dag räknas bara om båda:
      - jobbar (inte OFF)
      - har SAMMA passtyp (N1, N2 eller N3)
    """
    days_in_year = generate_year_data(year, person_id=None)
    
    stats: dict[int, dict] = {}
    for pid in PERSON_IDS:
        if pid == target_person_id:
            continue
        
        stats[pid] = {
            "other_id": pid,
            "other_name": persons[pid - 1].name,
            "total": 0,
            "by_shift": {
                "N1": 0,
                "N2": 0,
                "N3": 0
            },
        }
    
    for day in days_in_year:
        persons_today = day.get("persons", [])
        if not persons_today:
            continue
        
        target = next(
            (p for p in persons_today if p["person_id"] == target_person_id),
            None
        )
        if not target:
            continue
        
        target_shift = target.get("shift")
        if not target_shift or target_shift.code == "OFF":
            continue
        
        target_shift_code = target_shift.code
        
        for p in persons_today:
            pid = p["person_id"]
            if pid == target_person_id:
                continue

            other_shift = p.get("shift")
            if not other_shift or other_shift.code == "OFF":
                continue
            
            other_code = other_shift.code
            if other_code != target_shift_code:
                continue

            row = stats[pid]
            row["total"] += 1
            if target_shift_code in row["by_shift"]:
                row["by_shift"][target_shift_code] += 1
                
    rows = list(stats.values())
    rows.sort(key=lambda r: r["other_id"], reverse=False)
        
    return rows

def build_cowork_details(
    year: int,
    target_person_id: int,
    other_person_id: int,
) -> list[dict]:
    """
    Returnerar alla dagar då target_person_id och other_person_id
    jobbar SAMMA passtyp (N1/N2/N3) samma dag i ett år.

    Följer samma logik som build_cowork_stats:
      - båda måste ha arbetspass (inte OFF)
      - samma shift.code
    """
    days_in_year = generate_year_data(year, person_id=None)
    details: list[dict] = []

    for day in days_in_year:
        persons_today = day.get("persons", [])
        if not persons_today:
            continue

        target = next(
            (p for p in persons_today if p["person_id"] == target_person_id),
            None,
        )
        other = next(
            (p for p in persons_today if p["person_id"] == other_person_id),
            None,
        )

        if not target or not other:
            continue

        target_shift = target.get("shift")
        other_shift = other.get("shift")

        if (
            not target_shift
            or target_shift.code == "OFF"
            or not other_shift
            or other_shift.code == "OFF"
        ):
            continue

        # Bara dagar där ni har samma passtyp
        if target_shift.code != other_shift.code:
            continue

        details.append(
            {
                "date": day["date"],
                "weekday_name": day["weekday_name"],
                "rotation_week": target.get("rotation_week"),
                "target_id": target_person_id,
                "target_name": target["person_name"],
                "target_shift": target_shift,
                "other_id": other_person_id,
                "other_name": other["person_name"],
                "other_shift": other_shift,
            }
        )

    details.sort(key=lambda r: r["date"])
    return details
