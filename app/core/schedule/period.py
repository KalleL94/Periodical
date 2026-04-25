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
    get_rotation_length_for_date,
    get_rotation_start_date,
    get_settings,
    get_shift_types,
    get_vacation_shift,
    weekday_names,
)
from .ob import calculate_ob_hours, get_combined_rules_for_year
from .overtime import get_overtime_shift_for_date
from .person_history import get_current_person_for_position, get_person_for_date
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
    include_coworkers: bool = False,
    employment_start: datetime.date | None = None,
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
    sunday = monday + datetime.timedelta(days=6)
    person_ids = [person_id] if person_id is not None else list(PERSON_IDS)
    persons = _get_persons()
    shift_types = get_shift_types()

    rotation_to_user_id = _build_rotation_to_user_map(session, person_ids)

    # Batch fetch absences, overtime, oncall overrides, swaps, and shift overrides for the week
    absence_map = _batch_fetch_absences(session, person_ids, monday, sunday, rotation_to_user_id)
    ot_shift_map = _batch_fetch_ot_shifts(session, person_ids, monday, sunday, rotation_to_user_id)
    oncall_override_map = _batch_fetch_oncall_overrides(session, person_ids, monday, sunday, rotation_to_user_id)
    swap_map = _batch_fetch_swap_map(session, person_ids, monday, sunday, rotation_to_user_id)
    shift_override_map = _batch_fetch_shift_overrides(session, person_ids, monday, sunday, rotation_to_user_id)

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
                _build_person_day_basic(
                    current_date,
                    pid,
                    persons,
                    shift_types,
                    session,
                    None,
                    ot_shift_map,
                    absence_map,
                    oncall_override_map,
                    swap_map,
                    shift_override_map=shift_override_map,
                )
                for pid in person_ids
            ]
        else:
            day_info.update(
                _build_person_day_basic(
                    current_date,
                    person_id,
                    persons,
                    shift_types,
                    session,
                    None,
                    ot_shift_map,
                    absence_map,
                    oncall_override_map,
                    swap_map,
                    employment_start=employment_start,
                    shift_override_map=shift_override_map,
                )
            )

        days_in_week.append(day_info)

    # Add coworkers if requested
    if include_coworkers and person_id is not None:
        from .cowork import get_coworkers_for_day

        # Fetch all persons' data for the week for coworker matching
        all_persons_week = build_week_data(year, week, person_id=None, session=session)

        # Build lookup: date -> persons list
        persons_by_date = {day["date"]: day.get("persons", []) for day in all_persons_week}

        # Add coworkers to each day
        for day_info in days_in_week:
            if day_info.get("before_employment"):
                day_info["coworkers"] = []
                continue
            current_date = day_info["date"]
            actual_shift = day_info.get("shift")

            # For OT shifts with time-based matching, use a special marker
            # For regular shifts, use original_shift if available, otherwise actual shift
            if actual_shift and actual_shift.code == "OT":
                # Use OT as shift_code to trigger time-based matching
                original_shift = day_info.get("original_shift")
                # If original_shift is a work shift, use it; otherwise use "OT" for time matching
                if original_shift and original_shift.code in ("N1", "N2", "N3"):
                    shift_code = original_shift.code
                else:
                    shift_code = "OT"  # Will use time-based matching
            else:
                # Use actual_shift directly - if this person has a swap, actual_shift
                # already reflects the swapped shift code.
                shift_code = actual_shift.code if actual_shift else "OFF"

            persons_today = persons_by_date.get(current_date, [])
            target_start = day_info.get("start")
            target_end = day_info.get("end")
            coworkers = get_coworkers_for_day(person_id, shift_code, persons_today, target_start, target_end)
            day_info["coworkers"] = coworkers

    return days_in_week


def generate_period_data(
    start_date: datetime.date,
    end_date: datetime.date,
    person_id: int | None = None,
    session=None,
    user_wages: dict[int, int] | None = None,
    user_rates_map: dict[int, dict] | None = None,
    employment_start: datetime.date | None = None,
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
        user_rates_map: Per-user rate overrides {person_id: rates_dict}

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

    # Förbered person-lista
    person_ids = [person_id] if person_id is not None else list(PERSON_IDS)

    # Bygg mappning rotation_position -> user_id (hanterar Peter/Rickard som har olika user_id)
    rotation_to_user_id = _build_rotation_to_user_map(session, person_ids)

    # Batch fetch absences, overtime shifts, oncall overrides, swaps, and shift overrides for the entire period
    absence_map = _batch_fetch_absences(session, person_ids, effective_start, end_date, rotation_to_user_id)
    ot_shift_map = _batch_fetch_ot_shifts(session, person_ids, effective_start, end_date, rotation_to_user_id)
    oncall_override_map = _batch_fetch_oncall_overrides(
        session, person_ids, effective_start, end_date, rotation_to_user_id
    )
    swap_map = _batch_fetch_swap_map(session, person_ids, effective_start, end_date, rotation_to_user_id)
    shift_override_map = _batch_fetch_shift_overrides(
        session, person_ids, effective_start, end_date, rotation_to_user_id
    )

    # Generera dagdata
    persons = _get_persons()
    settings = get_settings()
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
                _build_person_day_basic(
                    current_day,
                    pid,
                    persons,
                    get_shift_types(),
                    session,
                    vacation_dates,
                    ot_shift_map,
                    absence_map,
                    oncall_override_map,
                    swap_map,
                    shift_override_map=shift_override_map,
                )
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
                ot_shift_map,
                absence_map,
                oncall_override_map,
                swap_map,
                user_rates_map=user_rates_map,
                employment_start=employment_start,
            )

        days_out.append(day_info)
        current_day += datetime.timedelta(days=1)

    return days_out


def generate_year_data(
    year: int,
    person_id: int | None = None,
    session=None,
    user_wages: dict[int, int] | None = None,
    user_rates_map: dict[int, dict] | None = None,
) -> list[dict]:
    """Genererar schemadat för ett helt år."""
    start_date = datetime.date(year, 1, 1)
    end_date = datetime.date(year, 12, 31)
    return generate_period_data(start_date, end_date, person_id, session, user_wages, user_rates_map)


def generate_month_data(
    year: int,
    month: int,
    person_id: int | None = None,
    session=None,
    user_wages: dict[int, int] | None = None,
    user_rates_map: dict[int, dict] | None = None,
    employment_start: datetime.date | None = None,
) -> list[dict]:
    """Genererar schemadat för en specifik månad."""
    start_date = datetime.date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end_date = datetime.date(year, month, last_day)
    return generate_period_data(
        start_date, end_date, person_id, session, user_wages, user_rates_map, employment_start=employment_start
    )


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


def _build_rotation_to_user_map(session, rotation_positions: list[int]) -> dict[int, int]:
    """
    Bygger mappning rotation_position -> user_id för användare där de skiljer sig.
    Returnerar rotation_position -> rotation_position som fallback om ingen match.
    """
    result = {p: p for p in rotation_positions}
    if not session:
        return result
    from app.database.database import User

    users = session.query(User).filter(User.person_id.in_(rotation_positions)).all()
    for u in users:
        if u.person_id is not None:
            result[u.person_id] = u.id
    return result


def _batch_fetch_absences(
    session,
    person_ids: list[int],
    start_date: datetime.date,
    end_date: datetime.date,
    rotation_to_user_id: dict[int, int] | None = None,
) -> dict[tuple[int, datetime.date], object]:
    """
    Batch-hämtar frånvaro för flera personer och en period.

    Returns:
        Dict med (rotation_position, date) -> Absence
    """
    if not session:
        return {}

    from app.database.database import Absence

    # Hämta faktiska user_ids (kan skilja sig från rotationsposition)
    if rotation_to_user_id:
        user_ids = list({rotation_to_user_id.get(p, p) for p in person_ids})
        user_id_to_rotation = {v: k for k, v in rotation_to_user_id.items()}
    else:
        user_ids = person_ids
        user_id_to_rotation = {}

    absences = (
        session.query(Absence)
        .filter(
            Absence.user_id.in_(user_ids),
            Absence.date >= start_date,
            Absence.date <= end_date,
        )
        .all()
    )

    return {(user_id_to_rotation.get(a.user_id, a.user_id), a.date): a for a in absences}


def _batch_fetch_ot_shifts(
    session,
    person_ids: list[int],
    start_date: datetime.date,
    end_date: datetime.date,
    rotation_to_user_id: dict[int, int] | None = None,
) -> dict[tuple[int, datetime.date], object]:
    """
    Batch-hämtar övertidspass för flera personer och en period.

    Returns:
        Dict med (rotation_position, date) -> OvertimeShift
    """
    if not session:
        return {}

    from app.database.database import OvertimeShift

    if rotation_to_user_id:
        user_ids = list({rotation_to_user_id.get(p, p) for p in person_ids})
        user_id_to_rotation = {v: k for k, v in rotation_to_user_id.items()}
    else:
        user_ids = person_ids
        user_id_to_rotation = {}

    # Hämta också dagen före start_date för att fånga OT som går över midnatt
    fetch_start = start_date - datetime.timedelta(days=1)

    ot_shifts = (
        session.query(OvertimeShift)
        .filter(
            OvertimeShift.user_id.in_(user_ids),
            OvertimeShift.date >= fetch_start,
            OvertimeShift.date <= end_date,
        )
        .all()
    )

    return {(user_id_to_rotation.get(s.user_id, s.user_id), s.date): s for s in ot_shifts}


def _batch_fetch_oncall_overrides(
    session,
    person_ids: list[int],
    start_date: datetime.date,
    end_date: datetime.date,
    rotation_to_user_id: dict[int, int] | None = None,
) -> dict[tuple[int, datetime.date], object]:
    """
    Batch-hämtar on-call overrides för flera personer och en period.

    Returns:
        Dict med (rotation_position, date) -> OnCallOverride
    """
    if not session:
        return {}

    from app.database.database import OnCallOverride

    if rotation_to_user_id:
        user_ids = list({rotation_to_user_id.get(p, p) for p in person_ids})
        user_id_to_rotation = {v: k for k, v in rotation_to_user_id.items()}
    else:
        user_ids = person_ids
        user_id_to_rotation = {}

    overrides = (
        session.query(OnCallOverride)
        .filter(
            OnCallOverride.user_id.in_(user_ids),
            OnCallOverride.date >= start_date,
            OnCallOverride.date <= end_date,
        )
        .all()
    )

    return {(user_id_to_rotation.get(o.user_id, o.user_id), o.date): o for o in overrides}


def _batch_fetch_shift_overrides(
    session,
    person_ids: list[int],
    start_date: datetime.date,
    end_date: datetime.date,
    rotation_to_user_id: dict[int, int] | None = None,
) -> dict[tuple[int, datetime.date], str]:
    """Batch-hämtar manuella passöverrides för flera personer och en period.

    Returns:
        Dict med (rotation_position, date) -> shift_code
    """
    if not session:
        return {}

    from app.database.database import ShiftOverride

    if rotation_to_user_id:
        user_ids = list({rotation_to_user_id.get(p, p) for p in person_ids})
        user_id_to_rotation = {v: k for k, v in rotation_to_user_id.items()}
    else:
        user_ids = person_ids
        user_id_to_rotation = {}

    overrides = (
        session.query(ShiftOverride)
        .filter(
            ShiftOverride.user_id.in_(user_ids),
            ShiftOverride.date >= start_date,
            ShiftOverride.date <= end_date,
        )
        .all()
    )

    return {(user_id_to_rotation.get(o.user_id, o.user_id), o.date): o.shift_code for o in overrides}


def _batch_fetch_swap_map(
    session,
    person_ids: list[int],
    start_date: datetime.date,
    end_date: datetime.date,
    rotation_to_user_id: dict[int, int] | None = None,
) -> dict[tuple[int, datetime.date], str]:
    """
    Batch-hämtar accepterade skiftbyten för flera personer och en period.

    Returns:
        Dict med (rotation_position, date) -> new_shift_code
    """
    if not session:
        return {}

    from app.database.database import ShiftSwap, SwapStatus, User

    if rotation_to_user_id:
        user_ids = list({rotation_to_user_id.get(p, p) for p in person_ids})
        user_id_to_rotation = {v: k for k, v in rotation_to_user_id.items()}
    else:
        user_ids = person_ids
        user_id_to_rotation = {}

    swaps = (
        session.query(ShiftSwap)
        .filter(
            ShiftSwap.status == SwapStatus.ACCEPTED,
            (ShiftSwap.requester_id.in_(user_ids) | ShiftSwap.target_id.in_(user_ids)),
            (
                ShiftSwap.requester_date.between(start_date, end_date)
                | ShiftSwap.target_date.between(start_date, end_date)
            ),
        )
        .all()
    )

    # Build full user_id -> rotation_person_id mapping for all swap participants
    all_swap_user_ids = set()
    for swap in swaps:
        all_swap_user_ids.update([swap.requester_id, swap.target_id])
    user_rotation = dict(user_id_to_rotation)  # start from known mappings
    if all_swap_user_ids:
        for u in session.query(User).filter(User.id.in_(all_swap_user_ids)).all():
            if u.id not in user_rotation:
                user_rotation[u.id] = u.person_id if u.person_id else u.id

    rotation_set = set(person_ids)
    swap_map = {}
    for swap in swaps:
        req_rot = user_rotation.get(swap.requester_id, swap.requester_id)
        tgt_rot = user_rotation.get(swap.target_id, swap.target_id)

        # On requester_date: they swap shifts
        if req_rot in rotation_set:
            # Requester gets what target normally has on this date
            tgt_result = determine_shift_for_date(swap.requester_date, tgt_rot)
            swap_map[(req_rot, swap.requester_date)] = tgt_result[0].code if tgt_result and tgt_result[0] else "OFF"
        if tgt_rot in rotation_set:
            # Target gets requester's shift on this date
            swap_map[(tgt_rot, swap.requester_date)] = swap.requester_shift_code or "OFF"

        # On target_date: they swap shifts
        if tgt_rot in rotation_set:
            # Target gets what requester normally has on this date
            req_result = determine_shift_for_date(swap.target_date, req_rot)
            swap_map[(tgt_rot, swap.target_date)] = req_result[0].code if req_result and req_result[0] else "OFF"
        if req_rot in rotation_set:
            # Requester gets target's shift on this date
            swap_map[(req_rot, swap.target_date)] = swap.target_shift_code or "OFF"

    return swap_map


def _build_person_day_basic(
    date: datetime.date,
    person_id: int,
    persons: list,
    shift_types: list,
    session=None,
    vacation_dates: dict[int, set[datetime.date]] | None = None,
    ot_shift_map: dict[tuple[int, datetime.date], object] | None = None,
    absence_map: dict[tuple[int, datetime.date], object] | None = None,
    oncall_override_map: dict[tuple[int, datetime.date], object] | None = None,
    swap_map: dict[tuple[int, datetime.date], str] | None = None,
    employment_start: datetime.date | None = None,
    shift_override_map: dict[tuple[int, datetime.date], str] | None = None,
) -> dict:
    """Bygger grundläggande dagdata för en person."""
    vacation_shift = get_vacation_shift()
    rotation_length = get_rotation_length_for_date(date)

    # Get person name via PersonHistory (shows correct person for this specific date)
    # Also check if date is before any person's employment at this position
    person_name = persons[person_id - 1].name  # Default fallback
    show_off_before_employment = False

    if session:
        # First check who held this position on this specific date
        date_person = get_person_for_date(session, person_id, date)
        if date_person:
            # Someone held this position on this date - use their name, no OFF
            person_name = date_person["name"]
        else:
            # No one held the position on this date - check if there's a future person
            current_person = get_current_person_for_position(session, person_id)
            if current_person:
                person_name = current_person["name"]
                # Only show OFF if date is before the current person's employment started
                if current_person.get("effective_from") and date < current_person["effective_from"]:
                    show_off_before_employment = True

    # Override: if the viewing user hasn't started yet, show before_employment
    if employment_start and date < employment_start and not show_off_before_employment:
        if session:
            current_person = get_current_person_for_position(session, person_id)
            if current_person:
                person_name = current_person["name"]
        show_off_before_employment = True

    # If date is before current person's employment, show OFF
    if show_off_before_employment:
        off_shift = next((s for s in shift_types if s.code == "OFF"), None)
        result = determine_shift_for_date(date, person_id)
        original_shift, rotation_week = result if result else (None, None)
        return {
            "person_id": person_id,
            "person_name": person_name,
            "shift": off_shift,
            "original_shift": original_shift,
            "rotation_week": rotation_week,
            "rotation_length": rotation_length,
            "hours": 0.0,
            "start": None,
            "end": None,
            "before_employment": True,  # Flag to indicate this is before employment
        }

    # Kolla frånvaro först (högsta prioritet) - använd batch-hämtad data
    absence = None
    if absence_map is not None:
        absence = absence_map.get((person_id, date))
    elif session:
        from app.database.database import Absence

        absence = session.query(Absence).filter(Absence.user_id == person_id, Absence.date == date).first()

    if absence:
        from app.database.database import AbsenceType

        # Partiell frånvaro: visa originalskiftet men med trunkerad sluttid
        if absence.left_at is not None and absence.absence_type != AbsenceType.VACATION:
            result = determine_shift_for_date(date, person_id)
            if result and result[0]:
                original_shift, rotation_week = result
                hours, start, end = calculate_shift_hours(date, original_shift.code)
                if start is not None and absence.left_at:
                    left_time = datetime.datetime.strptime(absence.left_at, "%H:%M").time()
                    end = datetime.datetime.combine(date, left_time)
                    if end <= start:
                        end = start  # säkerhetsventil
                    hours = (end - start).total_seconds() / 3600.0
                return {
                    "person_id": person_id,
                    "person_name": person_name,
                    "shift": original_shift,
                    "original_shift": original_shift,
                    "rotation_week": rotation_week,
                    "rotation_length": rotation_length,
                    "hours": hours,
                    "start": start,
                    "end": end,
                    "partial_absence": absence,
                }

        # VACATION absences use the SEM shift (same as week-based vacation)
        if absence.absence_type == AbsenceType.VACATION:
            absence_shift = vacation_shift
        else:
            absence_shift = next((s for s in shift_types if s.code == absence.absence_type.value), None)
        if absence_shift:
            result = determine_shift_for_date(date, person_id)
            original_shift, rotation_week = result if result else (None, None)
            return {
                "person_id": person_id,
                "person_name": person_name,
                "shift": absence_shift,
                "original_shift": original_shift,  # For coworker matching
                "rotation_week": rotation_week,
                "rotation_length": rotation_length,
                "hours": 0.0,
                "start": None,
                "end": None,
            }

    # Kolla semester
    if vacation_dates and vacation_shift and date in vacation_dates.get(person_id, set()):
        result = determine_shift_for_date(date, person_id)
        original_shift, rotation_week = result if result else (None, None)
        return {
            "person_id": person_id,
            "person_name": person_name,
            "shift": vacation_shift,
            "original_shift": original_shift,  # For coworker matching
            "rotation_week": rotation_week,
            "rotation_length": rotation_length,
            "hours": 0.0,
            "start": None,
            "end": None,
        }

    # Kolla manuell passöverride (admin-tilldelat N1/N2/N3 som ersätter rotation)
    if shift_override_map is not None:
        override_code = shift_override_map.get((person_id, date))
        if override_code:
            result = determine_shift_for_date(date, person_id)
            original_shift, rotation_week = result if result else (None, None)
            override_shift = next((s for s in shift_types if s.code == override_code), None)
            if override_shift:
                hours, start, end = calculate_shift_hours(date, override_shift.code)
                return {
                    "person_id": person_id,
                    "person_name": person_name,
                    "shift": override_shift,
                    "original_shift": original_shift,
                    "rotation_week": rotation_week,
                    "rotation_length": rotation_length,
                    "hours": hours,
                    "start": start,
                    "end": end,
                }

    # Kolla skiftbyte
    if swap_map is not None:
        new_code = swap_map.get((person_id, date))
        if new_code:
            result = determine_shift_for_date(date, person_id)
            original_shift, rotation_week = result if result else (None, None)
            swapped_shift = next((s for s in shift_types if s.code == new_code), None)
            if swapped_shift:
                hours, start, end = calculate_shift_hours(date, swapped_shift.code)
                return {
                    "person_id": person_id,
                    "person_name": person_name,
                    "shift": swapped_shift,
                    "original_shift": original_shift,
                    "rotation_week": rotation_week,
                    "rotation_length": rotation_length,
                    "hours": hours,
                    "start": start,
                    "end": end,
                }

    # Normalt skift
    result = determine_shift_for_date(date, person_id)
    if result is None or result[0] is None:
        shift, rotation_week = None, None
        hours, start, end = 0.0, None, None
    else:
        shift, rotation_week = result
        hours, start, end = calculate_shift_hours(date, shift.code)

    # Spara det ursprungliga skiftet för coworker-matchning
    original_shift = shift

    # Kolla oncall override - hämta från batch eller databas
    oncall_override = None
    if oncall_override_map is not None:
        oncall_override = oncall_override_map.get((person_id, date))
    elif session:
        from app.database.database import OnCallOverride

        oncall_override = (
            session.query(OnCallOverride)
            .filter(OnCallOverride.user_id == person_id, OnCallOverride.date == date)
            .first()
        )

    if oncall_override:
        from app.database.database import OnCallOverrideType

        if oncall_override.override_type == OnCallOverrideType.ADD:
            # Lägg till OC-pass - ersätt skiftet med OC
            oc_shift = next((s for s in shift_types if s.code == "OC"), None)
            if oc_shift:
                shift = oc_shift
                hours, start, end = 0.0, None, None  # OC har inga specifika tider
        elif oncall_override.override_type == OnCallOverrideType.REMOVE:
            # Ta bort OC-pass - om skiftet är OC, ersätt med OFF
            if shift and shift.code == "OC":
                off_shift = next((s for s in shift_types if s.code == "OFF"), None)
                if off_shift:
                    shift = off_shift
                    hours, start, end = 0.0, None, None

    # Kolla övertid på aktuell dag (för att visa som skift)
    ot_shift_for_display = None
    if ot_shift_map is not None:
        ot_shift_for_display = ot_shift_map.get((person_id, date))
    elif session:
        ot_shift_for_display = get_overtime_shift_for_date(session, person_id, date)

    # Kolla också föregående dag för OT som påverkar beredskap (men visas inte som skift)
    ot_shift_for_oncall = ot_shift_for_display
    if not ot_shift_for_oncall:
        prev_day = date - datetime.timedelta(days=1)
        if ot_shift_map is not None:
            prev_ot = ot_shift_map.get((person_id, prev_day))
        elif session:
            prev_ot = get_overtime_shift_for_date(session, person_id, prev_day)
        else:
            prev_ot = None

        if prev_ot:
            try:
                _, ot_end_dt = parse_ot_times(prev_ot, prev_day)
                if ot_end_dt.date() > prev_day:
                    # OT går över midnatt in i aktuell dag - används för beredskapsberäkning
                    ot_shift_for_oncall = prev_ot
            except ValueError:
                pass

    # Visa OT som skift bara om det är registrerat på aktuell dag
    # is_extension=True innebär att skiftet förlängs – visa originalskiftet kvar
    if ot_shift_for_display and not ot_shift_for_display.is_extension:
        ot_shift_type = next((s for s in shift_types if s.code == "OT"), None)
        if ot_shift_type:
            shift = ot_shift_type
            hours = ot_shift_for_display.hours
            try:
                start, end = parse_ot_times(ot_shift_for_display, date)
            except ValueError:
                start, end = None, None

    return {
        "person_id": person_id,
        "person_name": person_name,
        "shift": shift,
        "original_shift": original_shift,  # For coworker matching with OT shifts
        "rotation_week": rotation_week,
        "rotation_length": rotation_length,
        "hours": hours,
        "start": start,
        "end": end,
        "ot_shift_for_oncall": ot_shift_for_oncall,  # OT that affects on-call (may be from prev day)
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
    ot_shift_map: dict[tuple[int, datetime.date], object] | None = None,
    absence_map: dict[tuple[int, datetime.date], object] | None = None,
    oncall_override_map: dict[tuple[int, datetime.date], object] | None = None,
    swap_map: dict[tuple[int, datetime.date], str] | None = None,
    user_rates_map: dict[int, dict] | None = None,
    employment_start: datetime.date | None = None,
) -> None:
    """Fyller i detaljerad daginfo för en person."""
    vacation_shift = get_vacation_shift()
    shift_types = get_shift_types()

    # Get person name via PersonHistory (shows correct person for this specific date)
    person_name = persons[person_id - 1].name  # Default fallback
    show_off_before_employment = False

    if session:
        # First check who held this position on this specific date
        date_person = get_person_for_date(session, person_id, current_day)
        if date_person:
            # Someone held this position on this date - use their name, no OFF
            person_name = date_person["name"]
        else:
            # No one held the position on this date - check if there's a future person
            current_person = get_current_person_for_position(session, person_id)
            if current_person:
                person_name = current_person["name"]
                # Only show OFF if date is before the current person's employment started
                if current_person.get("effective_from") and current_day < current_person["effective_from"]:
                    show_off_before_employment = True

    # Override: if the viewing user hasn't started yet, show before_employment
    # regardless of who held the position historically (e.g., their predecessor)
    if employment_start and current_day < employment_start and not show_off_before_employment:
        if session:
            current_person = get_current_person_for_position(session, person_id)
            if current_person:
                person_name = current_person["name"]
        show_off_before_employment = True

    # If date is before current person's employment, show OFF
    if show_off_before_employment:
        off_shift = next((s for s in shift_types if s.code == "OFF"), None)
        result = determine_shift_for_date(current_day, person_id)
        original_shift, rotation_week = result if result else (None, None)
        day_info.update(
            {
                "person_id": person_id,
                "person_name": person_name,
                "shift": off_shift,
                "original_shift": original_shift,
                "rotation_week": rotation_week,
                "hours": 0.0,
                "start": None,
                "end": None,
                "ob": {},
                "oncall_pay": 0.0,
                "oncall_details": {},
                "ot_pay": 0.0,
                "ot_hours": 0.0,
                "ot_details": {},
                "before_employment": True,
            }
        )
        return

    # Kolla frånvaro först (högsta prioritet) - använd batch-hämtad data
    absence = None
    if absence_map is not None:
        absence = absence_map.get((person_id, current_day))
    elif session:
        from app.database.database import Absence

        absence = session.query(Absence).filter(Absence.user_id == person_id, Absence.date == current_day).first()

    if absence:
        from app.database.database import AbsenceType

        # Partiell frånvaro: beräkna OB och timmar för jobbad del av passet
        if absence.left_at is not None and absence.absence_type != AbsenceType.VACATION:
            result = determine_shift_for_date(current_day, person_id)
            if result and result[0]:
                original_shift, rotation_week = result
                hours, start, end = calculate_shift_hours(current_day, original_shift.code)
                if start is not None and absence.left_at:
                    left_time = datetime.datetime.strptime(absence.left_at, "%H:%M").time()
                    end = datetime.datetime.combine(current_day, left_time)
                    if end <= start:
                        end = start
                    hours = (end - start).total_seconds() / 3600.0
                    ob = calculate_ob_hours(start, end, combined_ob_rules) if original_shift.code != "OC" else {}
                else:
                    ob = {}

                day_info.update(
                    {
                        "person_id": person_id,
                        "person_name": person_name,
                        "shift": original_shift,
                        "original_shift": original_shift,
                        "rotation_week": rotation_week,
                        "hours": hours,
                        "start": start,
                        "end": end,
                        "ob": ob,
                        "oncall_pay": 0.0,
                        "oncall_details": {},
                        "ot_pay": 0.0,
                        "ot_hours": 0.0,
                        "ot_details": {},
                        "partial_absence": absence,
                    }
                )
                return

        # VACATION absences use the SEM shift (same as week-based vacation)
        if absence.absence_type == AbsenceType.VACATION:
            absence_shift = vacation_shift
        else:
            absence_shift = next((s for s in shift_types if s.code == absence.absence_type.value), None)
        if absence_shift:
            shift = absence_shift
            # Get original shift for coworker matching
            result = determine_shift_for_date(current_day, person_id)
            original_shift, rotation_week = result if result else (None, None)
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
                    "person_name": person_name,
                    "shift": shift,
                    "original_shift": original_shift,  # For coworker matching
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
    elif swap_map is not None and (person_id, current_day) in swap_map:
        # Skiftbyte: ersätt med det nya skiftet
        new_code = swap_map[(person_id, current_day)]
        result = determine_shift_for_date(current_day, person_id)
        rotation_week = result[1] if result else None
        swapped_shift = next((s for s in shift_types if s.code == new_code), None)
        if swapped_shift:
            shift = swapped_shift
            hours, start, end = calculate_shift_hours(current_day, shift.code)
            if start is not None and shift.code != "OC":
                ob = calculate_ob_hours(start, end, combined_ob_rules)
            else:
                ob = {}
        else:
            shift, rotation_week = None, None
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

    # Spara det ursprungliga skiftet för coworker-matchning
    original_shift = shift

    # Kolla oncall override - hämta från batch eller databas
    oncall_override = None
    if oncall_override_map is not None:
        oncall_override = oncall_override_map.get((person_id, current_day))
    elif session:
        from app.database.database import OnCallOverride

        oncall_override = (
            session.query(OnCallOverride)
            .filter(OnCallOverride.user_id == person_id, OnCallOverride.date == current_day)
            .first()
        )

    if oncall_override:
        from app.database.database import OnCallOverrideType

        if oncall_override.override_type == OnCallOverrideType.ADD:
            # Lägg till OC-pass - ersätt skiftet med OC
            oc_shift = next((s for s in shift_types if s.code == "OC"), None)
            if oc_shift:
                shift = oc_shift
                hours, start, end = 0.0, None, None  # OC har inga specifika tider
                ob = {}
        elif oncall_override.override_type == OnCallOverrideType.REMOVE:
            # Ta bort OC-pass - om skiftet är OC, ersätt med OFF
            if shift and shift.code == "OC":
                off_shift = next((s for s in shift_types if s.code == "OFF"), None)
                if off_shift:
                    shift = off_shift
                    hours, start, end = 0.0, None, None
                    ob = {}

    # Beräkna beredskap-ersättning
    oncall_pay = 0.0
    oncall_details = {}
    _person_rates = (user_rates_map or {}).get(person_id) or {}
    if shift and shift.code == "OC":
        oncall_rules = get_oncall_rules(current_day.year)
        oncall_calc = calculate_oncall_pay(
            current_day,
            user_wages.get(person_id, settings.monthly_salary),
            oncall_rules,
            rate_overrides=_person_rates.get("oncall"),
        )
        oncall_pay = oncall_calc["total_pay"]
        oncall_details = oncall_calc

    # Kolla övertid - både på aktuell dag (för visning) och föregående dag (för beredskap)
    ot_shift = None
    if ot_shift_map is not None:
        ot_shift = ot_shift_map.get((person_id, current_day))
    elif session:
        ot_shift = get_overtime_shift_for_date(session, person_id, current_day)

    # Check previous day for OT that crosses midnight (affects on-call but not displayed as OT)
    ot_shift_for_oncall = ot_shift
    if not ot_shift_for_oncall:
        prev_day = current_day - datetime.timedelta(days=1)
        if ot_shift_map is not None:
            prev_ot = ot_shift_map.get((person_id, prev_day))
        elif session:
            prev_ot = get_overtime_shift_for_date(session, person_id, prev_day)
        else:
            prev_ot = None

        if prev_ot:
            try:
                _, ot_end_dt = parse_ot_times(prev_ot, prev_day)
                if ot_end_dt.date() > prev_day:
                    # OT crosses midnight into current day - used for on-call calc
                    ot_shift_for_oncall = prev_ot
            except ValueError:
                pass

    # Om beredskap + övertid (samma dag ELLER föregående dag över midnatt), räkna om beredskap
    if shift and shift.code == "OC" and ot_shift_for_oncall:
        oncall_pay, oncall_details = _recalculate_oncall_before_ot(
            current_day,
            ot_shift_for_oncall,
            user_wages,
            person_id,
            settings,
            oncall_rate_overrides=_person_rates.get("oncall"),
        )

    ot_pay = 0.0
    ot_hours = 0.0
    ot_details = {}

    if ot_shift:
        # Beräkna övertidsersättning med temporal wage query
        from app.core.constants import OT_RATE_DIVISOR

        from .wages import get_user_wage

        # Get wage for this specific date (temporal query)
        wage_for_date = get_user_wage(session, person_id, settings.monthly_salary, effective_date=current_day)
        _ot_custom = _person_rates.get("ot")
        hourly_rate = float(_ot_custom) if _ot_custom is not None else (wage_for_date / OT_RATE_DIVISOR)

        # Recalculate overtime pay based on historical wage
        ot_hours = ot_shift.hours
        ot_pay = hourly_rate * ot_hours

        ot_details = {
            "start_time": str(ot_shift.start_time),
            "end_time": str(ot_shift.end_time),
            "hours": ot_hours,
            "pay": ot_pay,
            "hourly_rate": hourly_rate,
            "is_extension": ot_shift.is_extension,
        }

        # Ersätt skift med OT för visning – men inte om det är en förlängning
        if not ot_shift.is_extension:
            ot_shift_type = next((s for s in shift_types if s.code == "OT"), None)
            if ot_shift_type:
                shift = ot_shift_type
                hours = ot_shift.hours
                try:
                    start, end = parse_ot_times(ot_shift, current_day)
                except ValueError:
                    start, end = None, None

    day_info.update(
        {
            "person_id": person_id,
            "person_name": person_name,
            "shift": shift,
            "original_shift": original_shift,  # For coworker matching with OT shifts
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
    oncall_rate_overrides: dict[str, int | float] | None = None,
) -> tuple[float, dict]:
    """Räknar om beredskap-ersättning för perioden före OCH efter övertid.

    Beredskap betalas för 24h minus övertidstimmar.
    Ex: 24h beredskap - 8.5h övertid = 15.5h beredskapsersättning
    """
    day_start = datetime.datetime.combine(current_day, dt_time(0, 0))
    day_end = datetime.datetime.combine(current_day + datetime.timedelta(days=1), dt_time(0, 0))

    try:
        # Use ot_shift.date for parsing, not current_day (in case OT crosses midnight)
        ot_start_dt, ot_end_dt = parse_ot_times(ot_shift, ot_shift.date)
    except ValueError:
        # Om parsing misslyckas, betala ingen beredskap
        return 0.0, {"note": "Could not parse OT times", "total_pay": 0.0}

    oncall_rules = get_oncall_rules(current_day.year)
    monthly_salary = user_wages.get(person_id, settings.monthly_salary)

    total_pay = 0.0
    combined_breakdown = {}
    combined_details = {
        "periods": [],
        "total_pay": 0.0,
        "total_hours": 0.0,
    }

    # Period 1: Före övertid (00:00 till OT start)
    if ot_start_dt > day_start:
        period1 = calculate_oncall_pay_for_period(
            day_start,
            ot_start_dt,
            monthly_salary,
            oncall_rules,
            rate_overrides=oncall_rate_overrides,
        )
        total_pay += period1["total_pay"]
        combined_details["periods"].append(
            {
                "start": day_start,
                "end": ot_start_dt,
                "hours": period1["total_hours"],
                "pay": period1["total_pay"],
            }
        )
        combined_details["total_hours"] += period1["total_hours"]

        # Merge breakdown
        for code, data in period1["breakdown"].items():
            if code not in combined_breakdown:
                combined_breakdown[code] = data.copy()
            else:
                combined_breakdown[code]["hours"] += data["hours"]
                combined_breakdown[code]["pay"] += data["pay"]

    # Period 2: Efter övertid (OT slut till 24:00)
    if ot_end_dt < day_end:
        period2 = calculate_oncall_pay_for_period(
            ot_end_dt,
            day_end,
            monthly_salary,
            oncall_rules,
            rate_overrides=oncall_rate_overrides,
        )
        total_pay += period2["total_pay"]
        combined_details["periods"].append(
            {
                "start": ot_end_dt,
                "end": day_end,
                "hours": period2["total_hours"],
                "pay": period2["total_pay"],
            }
        )
        combined_details["total_hours"] += period2["total_hours"]

        # Merge breakdown
        for code, data in period2["breakdown"].items():
            if code not in combined_breakdown:
                combined_breakdown[code] = data.copy()
            else:
                combined_breakdown[code]["hours"] += data["hours"]
                combined_breakdown[code]["pay"] += data["pay"]

    combined_details["breakdown"] = combined_breakdown
    combined_details["total_pay"] = total_pay

    return total_pay, combined_details
