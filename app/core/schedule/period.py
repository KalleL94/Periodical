"""Generering av schemaperioder."""

import calendar
import datetime
from datetime import time as dt_time

from app.core.constants import PERSON_IDS
from app.core.oncall import _cached_oncall_rules as get_oncall_rules
from app.core.oncall import calculate_oncall_pay, calculate_oncall_pay_for_period
from app.core.storage import load_persons
from app.core.time_utils import parse_ot_times

from .core import (
    calculate_shift_hours,
    determine_shift_for_date,
    get_rotation_start_date,
    get_settings,
    get_shift_types,
    get_vacation_shift,
    weekday_names,
)
from .ob import calculate_ob_hours, get_combined_rules_for_year
from .overtime import get_overtime_shift_for_date
from .vacation import get_vacation_dates_for_year
from .wages import get_all_user_wages

_persons = None


def _get_persons():
    global _persons
    if _persons is None:
        _persons = load_persons()
    return _persons


def build_week_data(
    year: int,
    week: int,
    person_id: int | None = None,
    session=None,
) -> list[dict]:
    """
    Bygger veckodata för ett år/vecka.

    Args:
        year: År
        week: Veckonummer (ISO)
        person_id: Om None, returneras alla personer per dag
        session: SQLAlchemy session för DB-queries

    Returns:
        Lista med 7 dagar, varje dag innehåller skiftinfo
    """
    monday = datetime.date.fromisocalendar(year, week, 1)
    person_ids = [person_id] if person_id is not None else list(PERSON_IDS)
    persons = _get_persons()
    shift_types = get_shift_types()
    days_in_week = []

    for offset in range(7):
        current_date = monday + datetime.timedelta(days=offset)
        day_info = {
            "date": current_date,
            "weekday_index": current_date.weekday(),
            "weekday_name": weekday_names[current_date.weekday()],
        }

        if person_id is None:
            day_info["persons"] = [
                _build_person_day_basic(current_date, pid, persons, shift_types, session) for pid in person_ids
            ]
        else:
            day_info.update(_build_person_day_basic(current_date, person_id, persons, shift_types, session))

        days_in_week.append(day_info)

    return days_in_week


def generate_period_data(
    start_date: datetime.date,
    end_date: datetime.date,
    person_id: int | None = None,
    session=None,
    user_wages: dict[int, int] | None = None,
) -> list[dict]:
    """
    Genererar schemadat för en godtycklig period.

    Detta är kärnfunktionen som generate_year_data() och generate_month_data()
    använder internt.

    Args:
        start_date: Första datum
        end_date: Sista datum
        person_id: Om None, returneras alla personer per dag
        session: SQLAlchemy session
        user_wages: Förladdade löner (undviker N+1 queries)

    Returns:
        Lista med dagdata
    """
    rotation_start = get_rotation_start_date()

    # Justera startdatum om rotation inte börjat
    effective_start = max(start_date, rotation_start)
    if effective_start > end_date:
        return []

    # Samla år i perioden för OB-regler
    years_in_range = _get_years_in_range(effective_start, end_date)

    # Bygg kombinerade OB-regler
    combined_ob_rules = []
    for yr in years_in_range:
        combined_ob_rules.extend(get_combined_rules_for_year(yr))

    # Ladda semesterdatum
    vacation_dates = _load_vacation_dates(years_in_range)

    # Ladda löner om inte redan gjort
    if user_wages is None:
        user_wages = get_all_user_wages(session)

    # Generera dagdata
    persons = _get_persons()
    settings = get_settings()
    person_ids = [person_id] if person_id is not None else list(PERSON_IDS)
    days_out = []

    current_day = effective_start
    while current_day <= end_date:
        day_info = {
            "date": current_day,
            "weekday_index": current_day.weekday(),
            "weekday_name": weekday_names[current_day.weekday()],
        }

        if person_id is None:
            day_info["persons"] = [
                _build_person_day_basic(current_day, pid, persons, get_shift_types(), session, vacation_dates)
                for pid in person_ids
            ]
        else:
            _populate_single_person_day(
                day_info,
                current_day,
                person_id,
                vacation_dates,
                combined_ob_rules,
                user_wages,
                session,
                persons,
                settings,
            )

        days_out.append(day_info)
        current_day += datetime.timedelta(days=1)

    return days_out


def generate_year_data(
    year: int,
    person_id: int | None = None,
    session=None,
    user_wages: dict[int, int] | None = None,
) -> list[dict]:
    """Genererar schemadat för ett helt år."""
    start_date = datetime.date(year, 1, 1)
    end_date = datetime.date(year, 12, 31)
    return generate_period_data(start_date, end_date, person_id, session, user_wages)


def generate_month_data(
    year: int,
    month: int,
    person_id: int | None = None,
    session=None,
    user_wages: dict[int, int] | None = None,
) -> list[dict]:
    """Genererar schemadat för en specifik månad."""
    start_date = datetime.date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end_date = datetime.date(year, month, last_day)
    return generate_period_data(start_date, end_date, person_id, session, user_wages)


# === Privata hjälpfunktioner ===


def _get_years_in_range(start: datetime.date, end: datetime.date) -> set[int]:
    """Returnerar alla år som finns i ett datumintervall."""
    years = set()
    temp = start
    while temp <= end:
        years.add(temp.year)
        temp += datetime.timedelta(days=365)
    years.add(end.year)
    return years


def _load_vacation_dates(years: set[int]) -> dict[int, set[datetime.date]]:
    """Laddar semesterdatum för flera år."""
    vacation_dates: dict[int, set[datetime.date]] = {}
    for yr in years:
        year_vacations = get_vacation_dates_for_year(yr)
        for pid, dates in year_vacations.items():
            if pid not in vacation_dates:
                vacation_dates[pid] = set()
            vacation_dates[pid].update(dates)
    return vacation_dates


def _build_person_day_basic(
    date: datetime.date,
    person_id: int,
    persons: list,
    shift_types: list,
    session=None,
    vacation_dates: dict[int, set[datetime.date]] | None = None,
) -> dict:
    """Bygger grundläggande dagdata för en person."""
    vacation_shift = get_vacation_shift()

    # Kolla frånvaro först (högsta prioritet)
    if session:
        from app.database.database import Absence

        absence = session.query(Absence).filter(Absence.user_id == person_id, Absence.date == date).first()

        if absence:
            # Hitta rätt shift type för frånvarotypen
            absence_shift = next((s for s in shift_types if s.code == absence.absence_type.value), None)
            if absence_shift:
                result = determine_shift_for_date(date, person_id)
                _, rotation_week = result if result else (None, None)
                return {
                    "person_id": person_id,
                    "person_name": persons[person_id - 1].name,
                    "shift": absence_shift,
                    "rotation_week": rotation_week,
                    "hours": 0.0,
                    "start": None,
                    "end": None,
                }

    # Kolla semester
    if vacation_dates and vacation_shift and date in vacation_dates.get(person_id, set()):
        result = determine_shift_for_date(date, person_id)
        _, rotation_week = result if result else (None, None)
        return {
            "person_id": person_id,
            "person_name": persons[person_id - 1].name,
            "shift": vacation_shift,
            "rotation_week": rotation_week,
            "hours": 0.0,
            "start": None,
            "end": None,
        }

    # Normalt skift
    result = determine_shift_for_date(date, person_id)
    if result is None or result[0] is None:
        shift, rotation_week = None, None
        hours, start, end = 0.0, None, None
    else:
        shift, rotation_week = result
        hours, start, end = calculate_shift_hours(date, shift.code)

    # Kolla övertid
    if session:
        ot_shift = get_overtime_shift_for_date(session, person_id, date)
        if ot_shift:
            ot_shift_type = next((s for s in shift_types if s.code == "OT"), None)
            if ot_shift_type:
                shift = ot_shift_type
                hours = ot_shift.hours
                try:
                    start, end = parse_ot_times(ot_shift, date)
                except ValueError:
                    start, end = None, None

    return {
        "person_id": person_id,
        "person_name": persons[person_id - 1].name,
        "shift": shift,
        "rotation_week": rotation_week,
        "hours": hours,
        "start": start,
        "end": end,
    }


def _populate_single_person_day(
    day_info: dict,
    current_day: datetime.date,
    person_id: int,
    vacation_dates: dict[int, set[datetime.date]],
    combined_ob_rules: list,
    user_wages: dict[int, int],
    session,
    persons: list,
    settings,
) -> None:
    """Fyller i detaljerad daginfo för en person."""
    vacation_shift = get_vacation_shift()
    shift_types = get_shift_types()

    # Kolla frånvaro först (högsta prioritet)
    if session:
        from app.database.database import Absence

        absence = session.query(Absence).filter(Absence.user_id == person_id, Absence.date == current_day).first()

        if absence:
            # Hitta rätt shift type för frånvarotypen
            absence_shift = next((s for s in shift_types if s.code == absence.absence_type.value), None)
            if absence_shift:
                shift = absence_shift
                rotation_week = None
                hours, start, end = 0.0, None, None
                ob = {}
                oncall_pay = 0.0
                oncall_details = {}
                ot_pay = 0.0
                ot_hours = 0.0
                ot_details = {}

                day_info.update(
                    {
                        "person_id": person_id,
                        "person_name": persons[person_id - 1].name,
                        "shift": shift,
                        "rotation_week": rotation_week,
                        "hours": hours,
                        "start": start,
                        "end": end,
                        "ob": ob,
                        "oncall_pay": oncall_pay,
                        "oncall_details": oncall_details,
                        "ot_pay": ot_pay,
                        "ot_hours": ot_hours,
                        "ot_details": ot_details,
                    }
                )
                return

    # Kolla semester
    if vacation_shift and current_day in vacation_dates.get(person_id, set()):
        shift = vacation_shift
        rotation_week = None
        hours, start, end = 0.0, None, None
        ob = {}
    else:
        result = determine_shift_for_date(current_day, person_id)
        if result is None or result[0] is None:
            shift, rotation_week = None, None
            hours, start, end = 0.0, None, None
            ob = {}
        else:
            shift, rotation_week = result
            hours, start, end = calculate_shift_hours(current_day, shift.code)
            if start is not None and shift.code != "OC":
                ob = calculate_ob_hours(start, end, combined_ob_rules)
            else:
                ob = {}

    # Beräkna jour-ersättning
    oncall_pay = 0.0
    oncall_details = {}
    if shift and shift.code == "OC":
        oncall_rules = get_oncall_rules(current_day.year)
        oncall_calc = calculate_oncall_pay(
            current_day,
            user_wages.get(person_id, settings.monthly_salary),
            oncall_rules,
        )
        oncall_pay = oncall_calc["total_pay"]
        oncall_details = oncall_calc

    # Kolla övertid
    ot_shift = get_overtime_shift_for_date(session, person_id, current_day) if session else None
    ot_pay = 0.0
    ot_hours = 0.0
    ot_details = {}

    if ot_shift:
        # Om jour + övertid, räkna om jour för perioden före övertid
        if shift and shift.code == "OC":
            oncall_pay, oncall_details = _recalculate_oncall_before_ot(
                current_day, ot_shift, user_wages, person_id, settings
            )

        # Beräkna övertidsersättning
        from app.core.constants import OT_RATE_DIVISOR

        ot_pay = ot_shift.ot_pay
        ot_hours = ot_shift.hours
        ot_details = {
            "start_time": str(ot_shift.start_time),
            "end_time": str(ot_shift.end_time),
            "hours": ot_hours,
            "pay": ot_pay,
            "hourly_rate": user_wages.get(person_id, settings.monthly_salary) / OT_RATE_DIVISOR,
        }

        # Ersätt skift med OT för visning
        ot_shift_type = next((s for s in shift_types if s.code == "OT"), None)
        if ot_shift_type:
            shift = ot_shift_type
            hours = ot_shift.hours
            try:
                start, end = parse_ot_times(ot_shift, current_day)
            except ValueError:
                pass

    day_info.update(
        {
            "person_id": person_id,
            "person_name": persons[person_id - 1].name,
            "shift": shift,
            "rotation_week": rotation_week,
            "hours": hours,
            "start": start,
            "end": end,
            "ob": ob,
            "oncall_pay": oncall_pay,
            "oncall_details": oncall_details,
            "ot_pay": ot_pay,
            "ot_hours": ot_hours,
            "ot_details": ot_details,
        }
    )


def _recalculate_oncall_before_ot(
    current_day: datetime.date,
    ot_shift,
    user_wages: dict[int, int],
    person_id: int,
    settings,
) -> tuple[float, dict]:
    """Räknar om jour-ersättning för perioden före övertid."""
    oc_start = datetime.datetime.combine(current_day, dt_time(0, 0))

    try:
        ot_start_dt, _ = parse_ot_times(ot_shift, current_day)
        oc_end = ot_start_dt
    except ValueError:
        oc_end = datetime.datetime.combine(current_day, dt_time(0, 0))

    oncall_rules = get_oncall_rules(current_day.year)

    if oc_end > oc_start:
        oncall_calc = calculate_oncall_pay_for_period(
            oc_start,
            oc_end,
            user_wages.get(person_id, settings.monthly_salary),
            oncall_rules,
        )
        return oncall_calc["total_pay"], oncall_calc
    else:
        return 0.0, {"note": "OT starts at or before OC start", "total_pay": 0.0}
