"""Schedule period generation."""

import calendar
import datetime
from dataclasses import dataclass
from datetime import time as dt_time
from typing import NamedTuple

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
from .person_history import get_current_person_for_position, get_person_for_date, get_position_vacancy
from .vacation import get_parental_dates_for_year, get_vacation_dates_for_year
from .wages import get_all_user_wages

_persons = None


@dataclass
class DayLookupContext:
    """Pre-fetched period-wide data, static across all days in a schedule generation call."""

    persons: list
    vacation_dates: dict
    parental_dates: dict
    ot_shift_map: dict | None
    absence_map: dict | None
    oncall_override_map: dict | None
    swap_map: dict | None
    shift_override_map: dict | None
    # Only populated in generate_period_data (not used by _build_person_day_basic):
    combined_ob_rules: list | None = None
    user_wages: dict | None = None
    settings: object = None
    user_rates_map: dict | None = None
    day_pay_override_map: dict | None = None
    # Linked-substitute data for the single-person view (issue #290): populated in
    # generate_period_data when the viewed position's holder has linked substitutes,
    # so the before-employment branch can render their pre-employment shifts.
    linked_subs: list | None = None
    linked_sub_shift_map: dict | None = None
    linked_sub_absence_map: dict | None = None
    linked_sub_ot_map: dict | None = None


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
    employment_end: datetime.date | None = None,
) -> list[dict]:
    """
    Bygger veckodata för ett år/vecka.

    Args:
        year: År
        week: Veckonummer (ISO)
        person_id: Om None, returneras alla personer per dag
        session: SQLAlchemy session för DB-queries
        employment_start: Mask days before this date to OFF (viewer not yet employed)
        employment_end: Mask days after this date to OFF (viewer's own employment
            for this position has ended, with or without a successor since taking
            over - their page must not show a successor's real schedule)

    Returns:
        Lista med 7 dagar, varje dag innehåller skiftinfo
    """
    monday = datetime.date.fromisocalendar(year, week, 1)
    sunday = monday + datetime.timedelta(days=6)
    person_ids = [person_id] if person_id is not None else list(PERSON_IDS)
    persons = _get_persons()

    rotation_to_user_id = _build_rotation_to_user_map(session, person_ids)

    # For a single-position personal view, also fetch rows for every other user who
    # held that position at any point in the week (past/future holder across a swap
    # or succession); the all-persons view already covers every active holder.
    extra_user_ids = _range_holder_user_ids(session, person_ids, monday, sunday) if person_id is not None else None

    # Batch fetch absences, overtime, oncall overrides, swaps, and shift overrides for the week
    absence_map = _batch_fetch_absences(session, person_ids, monday, sunday, rotation_to_user_id, extra_user_ids)
    ot_shift_map = _batch_fetch_ot_shifts(session, person_ids, monday, sunday, rotation_to_user_id, extra_user_ids)
    oncall_override_map = _batch_fetch_oncall_overrides(
        session, person_ids, monday, sunday, rotation_to_user_id, extra_user_ids
    )
    swap_map = _batch_fetch_swap_map(session, person_ids, monday, sunday, rotation_to_user_id, extra_user_ids)
    shift_override_map = _batch_fetch_shift_overrides(
        session, person_ids, monday, sunday, rotation_to_user_id, extra_user_ids
    )

    # Substitutes (vikarier) are only included in the all-persons view
    substitutes = _get_substitutes_with_shifts(session, monday, sunday) if person_id is None else []
    substitute_shift_map = _fetch_substitute_shifts(session, [s.id for s in substitutes], monday, sunday)
    sub_shift_types = get_shift_types() if substitutes else []

    years_week = _get_years_in_range(monday, sunday)
    vacation_dates = _load_vacation_dates(years_week, session=session)
    parental_dates = _load_parental_dates(years_week, session=session)

    ctx = DayLookupContext(
        persons=persons,
        vacation_dates=vacation_dates,
        parental_dates=parental_dates,
        ot_shift_map=ot_shift_map,
        absence_map=absence_map,
        oncall_override_map=oncall_override_map,
        swap_map=swap_map,
        shift_override_map=shift_override_map,
    )

    days_in_week = []

    for offset in range(7):
        current_date = monday + datetime.timedelta(days=offset)
        day_info = {
            "date": current_date,
            "weekday_index": current_date.weekday(),
            "weekday_name": weekday_names[current_date.weekday()],
        }

        if person_id is None:
            day_info["persons"] = [_build_person_day_basic(current_date, pid, ctx, session) for pid in person_ids] + [
                _build_substitute_day(current_date, s, substitute_shift_map, sub_shift_types) for s in substitutes
            ]
        else:
            day_info.update(_build_person_day_basic(current_date, person_id, ctx, session, employment_start))

        days_in_week.append(day_info)

    if person_id is not None and employment_end is not None:
        days_in_week = mask_days_to_employment(days_in_week, datetime.date.min, employment_end)

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
    include_substitutes: bool = False,
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

    # Ladda semester- och föräldraledighetsdatum
    vacation_dates = _load_vacation_dates(years_in_range, session=session)
    parental_dates = _load_parental_dates(years_in_range, session=session)

    # Ladda löner om inte redan gjort
    if user_wages is None:
        user_wages = get_all_user_wages(session)

    # Förbered person-lista
    person_ids = [person_id] if person_id is not None else list(PERSON_IDS)

    # Substitutes (vikarier): only when explicitly requested and building the all-persons view
    substitutes = (
        _get_substitutes_with_shifts(session, effective_start, end_date)
        if include_substitutes and person_id is None
        else []
    )
    substitute_shift_map = _fetch_substitute_shifts(session, [s.id for s in substitutes], effective_start, end_date)
    sub_shift_types = get_shift_types() if substitutes else []

    # Bygg mappning rotation_position -> user_id (hanterar Peter/Rickard som har olika user_id)
    rotation_to_user_id = _build_rotation_to_user_map(session, person_ids)

    # For a single-position personal view, also fetch rows for every other user who
    # held that position at any point in the range (past/future holder across a swap
    # or succession); the all-persons view already covers every active holder.
    extra_user_ids = (
        _range_holder_user_ids(session, person_ids, effective_start, end_date) if person_id is not None else None
    )

    # Batch fetch absences, overtime shifts, oncall overrides, swaps, and shift overrides for the entire period
    absence_map = _batch_fetch_absences(
        session, person_ids, effective_start, end_date, rotation_to_user_id, extra_user_ids
    )
    ot_shift_map = _batch_fetch_ot_shifts(
        session, person_ids, effective_start, end_date, rotation_to_user_id, extra_user_ids
    )
    oncall_override_map = _batch_fetch_oncall_overrides(
        session, person_ids, effective_start, end_date, rotation_to_user_id, extra_user_ids
    )
    swap_map = _batch_fetch_swap_map(
        session, person_ids, effective_start, end_date, rotation_to_user_id, extra_user_ids
    )
    shift_override_map = _batch_fetch_shift_overrides(
        session, person_ids, effective_start, end_date, rotation_to_user_id, extra_user_ids
    )
    day_pay_override_map = _batch_fetch_day_pay_overrides(
        session, person_ids, effective_start, end_date, rotation_to_user_id, extra_user_ids
    )

    # Linked substitutes (issue #290): for a single-person view, pre-fetch the shifts,
    # absences and overtime of any substitutes linked to the position's holder so the
    # before-employment branch in _populate_single_person_day can render them. Only
    # runs when the holder actually has linked substitutes.
    linked_subs: list | None = None
    linked_sub_shift_map: dict | None = None
    linked_sub_absence_map: dict | None = None
    linked_sub_ot_map: dict | None = None
    if person_id is not None and session:
        holder_user_id = rotation_to_user_id.get(person_id)
        subs = get_linked_substitutes_for_user(session, holder_user_id)
        if subs:
            linked_subs = subs
            linked_sub_ids = [s.id for s in subs]
            linked_sub_shift_map = _fetch_substitute_shifts(session, linked_sub_ids, effective_start, end_date)
            linked_sub_absence_map = _fetch_substitute_absences_by_date(
                session, linked_sub_ids, effective_start, end_date
            )
            linked_sub_ot_map = _fetch_substitute_ot_by_date(session, linked_sub_ids, effective_start, end_date)

    # Generera dagdata
    persons = _get_persons()
    settings = get_settings()

    ctx = DayLookupContext(
        persons=persons,
        vacation_dates=vacation_dates,
        parental_dates=parental_dates,
        ot_shift_map=ot_shift_map,
        absence_map=absence_map,
        oncall_override_map=oncall_override_map,
        swap_map=swap_map,
        shift_override_map=shift_override_map,
        combined_ob_rules=combined_ob_rules,
        user_wages=user_wages,
        settings=settings,
        user_rates_map=user_rates_map,
        day_pay_override_map=day_pay_override_map,
        linked_subs=linked_subs,
        linked_sub_shift_map=linked_sub_shift_map,
        linked_sub_absence_map=linked_sub_absence_map,
        linked_sub_ot_map=linked_sub_ot_map,
    )

    days_out = []

    current_day = effective_start
    while current_day <= end_date:
        day_info = {
            "date": current_day,
            "weekday_index": current_day.weekday(),
            "weekday_name": weekday_names[current_day.weekday()],
        }

        if person_id is None:
            day_info["persons"] = [_build_person_day_basic(current_day, pid, ctx, session) for pid in person_ids] + [
                _build_substitute_day(current_day, s, substitute_shift_map, sub_shift_types) for s in substitutes
            ]
        else:
            _populate_single_person_day(
                day_info,
                current_day,
                person_id,
                ctx,
                session,
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
    """Generates schedule data for a full year."""
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
    """Generates schedule data for a specific month."""
    start_date = datetime.date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end_date = datetime.date(year, month, last_day)
    return generate_period_data(
        start_date, end_date, person_id, session, user_wages, user_rates_map, employment_start=employment_start
    )


# === Privata hjälpfunktioner ===


def _get_years_in_range(start: datetime.date, end: datetime.date) -> set[int]:
    """Returns all years present in a date range."""
    years = set()
    temp = start
    while temp <= end:
        years.add(temp.year)
        temp += datetime.timedelta(days=365)
    years.add(end.year)
    return years


def _load_vacation_dates(years: set[int], session=None) -> dict[int, set[datetime.date]]:
    """Loads vacation dates for multiple years."""
    vacation_dates: dict[int, set[datetime.date]] = {}
    for yr in years:
        year_vacations = get_vacation_dates_for_year(yr, session=session)
        for pid, dates in year_vacations.items():
            if pid not in vacation_dates:
                vacation_dates[pid] = set()
            vacation_dates[pid].update(dates)
    return vacation_dates


def _load_parental_dates(years: set[int], session=None) -> dict[int, set[datetime.date]]:
    """Loads parental leave dates for multiple years."""
    parental_dates: dict[int, set[datetime.date]] = {}
    for yr in years:
        year_parentals = get_parental_dates_for_year(yr, session=session)
        for pid, dates in year_parentals.items():
            if pid not in parental_dates:
                parental_dates[pid] = set()
            parental_dates[pid].update(dates)
    return parental_dates


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


def _range_holder_user_ids(
    session, rotation_positions: list[int], start_date: datetime.date, end_date: datetime.date
) -> set[int]:
    """Return every user_id that held any of rotation_positions during a date range.

    _build_rotation_to_user_map only knows each position's CURRENT holder, so the
    batch helpers, keying off it, query just those users. For a narrow single-position
    view whose range sits on the far side of a swap or succession, the position's
    prior/future holder is never queried and their rows (overtime, on-call, absences,
    etc.) are structurally excluded before any per-row date re-attribution runs.

    Reading the PersonHistory segments overlapping [start_date, end_date] recovers the
    full set of holders in range. The per-row resolver in each batch helper then still
    buckets every fetched row onto the position its holder occupied on that row's date,
    so rows for a position not actually held on a given day fall outside the viewed
    position set and are harmlessly ignored.
    """
    from app.core.schedule.person_history import get_position_holder_segments

    user_ids: set[int] = set()
    if not session:
        return user_ids
    for pid in rotation_positions:
        for seg in get_position_holder_segments(session, pid, start_date, end_date):
            user_ids.add(seg["user_id"])
    return user_ids


def _build_user_position_resolver(session, user_ids, current_map: dict[int, int]):
    """Return a date-aware (user_id, date) -> rotation position resolver.

    Reads PersonHistory once so each fetched row is bucketed onto the position its
    user held ON THAT ROW'S DATE, not the user's current position. A position swap
    updates User.person_id immediately (even for a future-dated swap), so keying on
    current state alone would move a pre-swap row onto the successor's column.

    current_map (user_id -> current position) is the fallback for users that have no
    PersonHistory rows at all (legacy assignments), preserving existing behavior for
    them. Resolution mirrors get_user_person_id: prefer the record covering the date,
    otherwise the most recent record.
    """
    from app.database.database import PersonHistory

    segments: dict[int, list] = {}
    if session and user_ids:
        rows = (
            session.query(PersonHistory)
            .filter(PersonHistory.user_id.in_(list(user_ids)))
            .order_by(PersonHistory.effective_from.asc())
            .all()
        )
        for r in rows:
            segments.setdefault(r.user_id, []).append(r)

    def resolve(user_id: int, on_date: datetime.date) -> int:
        recs = segments.get(user_id)
        if not recs:
            return current_map.get(user_id, user_id)
        # recs are ascending by effective_from; the last covering match is the one
        # with the latest effective_from, matching get_user_person_id's ordering.
        covering = None
        for r in recs:
            if r.effective_from <= on_date and (r.effective_to is None or r.effective_to >= on_date):
                covering = r
        if covering is not None:
            return covering.person_id
        # No tenure covers the date: fall back to the most recent record.
        return recs[-1].person_id

    return resolve


def _get_substitutes_with_shifts(session, start_date: datetime.date, end_date: datetime.date) -> list:
    """Return active substitutes that have at least one shift in the range.

    Substitutes have an empty base schedule, so they are only shown on days they
    actually work. Listing only those with shifts in range keeps empty rows out of
    the week/month views.
    """
    if not session:
        return []
    from app.database.database import Substitute, SubstituteShift

    subs = session.query(Substitute).filter(Substitute.is_active == 1).all()
    if not subs:
        return []
    sub_ids = [s.id for s in subs]
    rows = (
        session.query(SubstituteShift.substitute_id)
        .filter(
            SubstituteShift.substitute_id.in_(sub_ids),
            SubstituteShift.date >= start_date,
            SubstituteShift.date <= end_date,
        )
        .distinct()
        .all()
    )
    active_ids = {r[0] for r in rows}
    return [s for s in subs if s.id in active_ids]


def get_linked_substitutes_for_user(session, user_id: int | None) -> list:
    """Return every substitute linked to a user account (issue #290).

    A user can in theory have been more than one substitute entity historically,
    so all linked rows are returned. Archived substitutes are included: their
    historical shifts still belong to the user's personal history.
    """
    if not session or user_id is None:
        return []
    from app.database.database import Substitute

    return session.query(Substitute).filter(Substitute.user_id == user_id).all()


def _fetch_substitute_shifts(
    session, sub_ids: list[int], start_date: datetime.date, end_date: datetime.date
) -> dict[tuple[int, datetime.date], object]:
    """Batch-fetch substitute shifts, keyed by (substitute_id, date)."""
    if not session or not sub_ids:
        return {}
    from app.database.database import SubstituteShift

    rows = (
        session.query(SubstituteShift)
        .filter(
            SubstituteShift.substitute_id.in_(sub_ids),
            SubstituteShift.date >= start_date,
            SubstituteShift.date <= end_date,
        )
        .all()
    )
    return {(r.substitute_id, r.date): r for r in rows}


def _substitute_absence_shift_code(absence_type) -> str:
    """Map a substitute's absence type to the shift code used to render it.

    Mirrors the agent mapping: VACATION shows as SEM, PARENTAL falls back to LEAVE,
    everything else uses the absence type's own code (SICK, VAB, LEAVE, OFF).
    """
    from app.database.database import AbsenceType

    if absence_type == AbsenceType.VACATION:
        return "SEM"
    if absence_type == AbsenceType.PARENTAL:
        return "LEAVE"
    return absence_type.value


def _build_substitute_day(
    date: datetime.date,
    sub,
    sub_shift_map: dict,
    shift_types: list,
    absence_map: dict | None = None,
    ot_map: dict | None = None,
) -> dict:
    """Build a single day's schedule dict for a substitute.

    The base schedule is OFF; a working shift only appears where a SubstituteShift exists.
    Priority mirrors the agent chain: an absence wins, then an overtime entry (shown as OT,
    like agents), then the scheduled shift, otherwise OFF.
    Matches the shape produced by _build_person_day_basic so the templates can render
    substitutes alongside regular persons. The person_id uses a "sub-<id>" namespace to
    avoid colliding with rotation positions (1-10).
    """
    rotation_length = get_rotation_length_for_date(date)
    off_shift = next((s for s in shift_types if s.code == "OFF"), None)
    pid = f"sub-{sub.id}"

    absence = absence_map.get((sub.id, date)) if absence_map else None
    if absence is not None:
        code = _substitute_absence_shift_code(absence.absence_type)
        absence_shift = next((s for s in shift_types if s.code == code), None)
        if absence_shift:
            return {
                "date": date,
                "person_id": pid,
                "substitute_id": sub.id,
                "person_name": sub.name,
                "shift": absence_shift,
                "original_shift": None,
                "rotation_week": None,
                "rotation_length": rotation_length,
                "hours": 0.0,
                "start": None,
                "end": None,
                "is_substitute": True,
                "is_absence": True,
            }

    # Overtime is shown as the OT shift (same convention as agents), taking display
    # priority over the scheduled shift. Substitute overtime is never an extension.
    ot_entries = ot_map.get((sub.id, date)) if ot_map else None
    ot_entry = ot_entries[0] if ot_entries else None
    if ot_entry is not None:
        ot_shift_type = next((s for s in shift_types if s.code == "OT"), None)
        if ot_shift_type:
            start = datetime.datetime.combine(date, ot_entry.start_time) if ot_entry.start_time else None
            end = datetime.datetime.combine(date, ot_entry.end_time) if ot_entry.end_time else None
            if start and end and end <= start:
                end += datetime.timedelta(days=1)
            return {
                "date": date,
                "person_id": pid,
                "substitute_id": sub.id,
                "person_name": sub.name,
                "shift": ot_shift_type,
                "original_shift": next(
                    (s for s in shift_types if s.code == sub_shift_map[(sub.id, date)].shift_code), None
                )
                if (sub.id, date) in sub_shift_map
                else None,
                "rotation_week": None,
                "rotation_length": rotation_length,
                "hours": ot_entry.hours or 0.0,
                "start": start,
                "end": end,
                "is_substitute": True,
            }

    entry = sub_shift_map.get((sub.id, date))
    if entry:
        shift = next((s for s in shift_types if s.code == entry.shift_code), None)
        if shift:
            hours, start, end = calculate_shift_hours(date, shift.code)
            return {
                "date": date,
                "person_id": pid,
                "substitute_id": sub.id,
                "person_name": sub.name,
                "shift": shift,
                "original_shift": None,
                "rotation_week": None,
                "rotation_length": rotation_length,
                "hours": hours,
                "start": start,
                "end": end,
                "is_substitute": True,
            }
    return {
        "date": date,
        "person_id": pid,
        "substitute_id": sub.id,
        "person_name": sub.name,
        "shift": off_shift,
        "original_shift": None,
        "rotation_week": None,
        "rotation_length": rotation_length,
        "hours": 0.0,
        "start": None,
        "end": None,
        "is_substitute": True,
    }


def _fetch_substitute_ot_by_date(
    session, sub_ids: list[int], start_date: datetime.date, end_date: datetime.date
) -> dict[tuple[int, datetime.date], list]:
    """Batch-fetch substitute overtime entries, keyed by (substitute_id, date)."""
    if not session or not sub_ids:
        return {}
    from app.database.database import OvertimeShift

    rows = (
        session.query(OvertimeShift)
        .filter(
            OvertimeShift.substitute_id.in_(sub_ids),
            OvertimeShift.date >= start_date,
            OvertimeShift.date <= end_date,
        )
        .order_by(OvertimeShift.date, OvertimeShift.start_time)
        .all()
    )
    by_date: dict[tuple[int, datetime.date], list] = {}
    for r in rows:
        by_date.setdefault((r.substitute_id, r.date), []).append(r)
    return by_date


def _fetch_substitute_ot_hours(
    session, sub_ids: list[int], start_date: datetime.date, end_date: datetime.date
) -> dict[int, float]:
    """Sum overtime hours per substitute for the range (no pay; hours only)."""
    if not session or not sub_ids:
        return {}
    from app.database.database import OvertimeShift

    rows = (
        session.query(OvertimeShift)
        .filter(
            OvertimeShift.substitute_id.in_(sub_ids),
            OvertimeShift.date >= start_date,
            OvertimeShift.date <= end_date,
        )
        .all()
    )
    totals: dict[int, float] = {}
    for r in rows:
        totals[r.substitute_id] = totals.get(r.substitute_id, 0.0) + (r.hours or 0.0)
    return totals


def _fetch_substitute_absences_by_date(
    session, sub_ids: list[int], start_date: datetime.date, end_date: datetime.date
) -> dict[tuple[int, datetime.date], object]:
    """Batch-fetch substitute absences, keyed by (substitute_id, date)."""
    if not session or not sub_ids:
        return {}
    from app.database.database import Absence

    rows = (
        session.query(Absence)
        .filter(
            Absence.substitute_id.in_(sub_ids),
            Absence.date >= start_date,
            Absence.date <= end_date,
        )
        .all()
    )
    return {(r.substitute_id, r.date): r for r in rows}


def _fetch_substitute_absence_counts(
    session, sub_ids: list[int], start_date: datetime.date, end_date: datetime.date
) -> dict[int, dict[str, int]]:
    """Count absence days per type per substitute for the range."""
    if not session or not sub_ids:
        return {}
    from app.database.database import Absence

    rows = (
        session.query(Absence)
        .filter(
            Absence.substitute_id.in_(sub_ids),
            Absence.date >= start_date,
            Absence.date <= end_date,
        )
        .all()
    )
    counts: dict[int, dict[str, int]] = {}
    for r in rows:
        bucket = counts.setdefault(r.substitute_id, {})
        key = str(r.absence_type)
        bucket[key] = bucket.get(key, 0) + 1
    return counts


def _get_substitutes_with_activity(
    session, start_date: datetime.date, end_date: datetime.date, include_overtime: bool = True
) -> list:
    """Active substitutes with activity in range: a shift or an absence (and optionally overtime).

    The month schedule view shows substitutes with a shift or an absence (an absence renders
    as a day shift). The report additionally pulls in overtime-only substitutes (include_overtime)
    so their hours appear in the totals even without a scheduled day.
    """
    if not session:
        return []
    from app.database.database import Absence, OvertimeShift, Substitute

    subs = session.query(Substitute).filter(Substitute.is_active == 1).all()
    if not subs:
        return []
    sub_ids = [s.id for s in subs]

    active_ids = {s.id for s in _get_substitutes_with_shifts(session, start_date, end_date)}
    models = [Absence, OvertimeShift] if include_overtime else [Absence]
    for model in models:
        rows = (
            session.query(model.substitute_id)
            .filter(
                model.substitute_id.in_(sub_ids),
                model.date >= start_date,
                model.date <= end_date,
            )
            .distinct()
            .all()
        )
        active_ids.update(r[0] for r in rows)

    return [s for s in subs if s.id in active_ids]


def mask_days_to_employment(days: list[dict], seg_from: datetime.date, seg_to: datetime.date) -> list[dict]:
    """
    Copy a position's generated day dicts, rendering days outside an employment
    segment as OFF (zero hours, no pay, before_employment flag) so a per-holder
    column only counts and displays that holder's own days.

    Days inside [seg_from, seg_to] are passed through unchanged (same object).
    Days outside are shallow-copied and zeroed so summarize_month_for_person
    contributes nothing for them, exactly like a real before-employment day.
    The zeroed keys mirror the before-employment early return in
    _populate_single_person_day; identity keys (date, person_id, rotation_week,
    weekday_name, etc.) are left intact.
    """
    shift_types = get_shift_types()
    off_shift = next((s for s in shift_types if s.code == "OFF"), None)
    masked: list[dict] = []
    for day in days:
        d = day.get("date")
        if d is not None and (d < seg_from or d > seg_to):
            copy = dict(day)
            copy["shift"] = off_shift
            copy["hours"] = 0.0
            copy["start"] = None
            copy["end"] = None
            copy["ob"] = {}
            copy["oncall_pay"] = 0.0
            copy["oncall_details"] = {}
            copy["ot_pay"] = 0.0
            copy["ot_hours"] = 0.0
            copy["ot_details"] = {}
            copy["ob_hours_override"] = None
            copy["before_employment"] = True
            # Clear week-based flags the summary counts independently of the shift,
            # so an out-of-segment day contributes no parental/partial-absence total.
            if "parental_leave" in copy:
                copy["parental_leave"] = False
            if "partial_absence" in copy:
                copy["partial_absence"] = None
            masked.append(copy)
        else:
            masked.append(day)
    return masked


def build_substitute_month_summaries(year: int, month: int, session, include_overtime: bool = False) -> list[dict]:
    """Build per-substitute month summaries (schedule only, no salary) for the month view.

    Each summary mirrors the shape produced by summarize_month_for_person enough for
    month_all.html to render: person_id, person_name, days and an empty ob_pay. It also
    carries aggregate totals (hours, overtime, on-call, absence days per type) used by the
    monthly report. Substitutes have no salary, so all pay figures are zero.

    Substitutes with a scheduled shift or an absence in the month are returned (an absence
    renders as a day shift). With include_overtime=True, overtime-only substitutes are also
    included so their hours appear in the report totals (used by the report).
    """
    start_date = datetime.date(year, month, 1)
    end_date = datetime.date(year, month, calendar.monthrange(year, month)[1])
    substitutes = _get_substitutes_with_activity(session, start_date, end_date, include_overtime=include_overtime)
    if not substitutes:
        return []

    sub_ids = [s.id for s in substitutes]
    shift_map = _fetch_substitute_shifts(session, sub_ids, start_date, end_date)
    ot_hours_map = _fetch_substitute_ot_hours(session, sub_ids, start_date, end_date)
    ot_by_date = _fetch_substitute_ot_by_date(session, sub_ids, start_date, end_date)
    absence_counts = _fetch_substitute_absence_counts(session, sub_ids, start_date, end_date)
    absence_by_date = _fetch_substitute_absences_by_date(session, sub_ids, start_date, end_date)
    shift_types = get_shift_types()
    settings = get_settings()

    # OB rules for the month (same rules agents use); covers any year in range.
    combined_ob_rules: list = []
    for yr in {start_date.year, end_date.year}:
        combined_ob_rules.extend(get_combined_rules_for_year(yr))

    summaries = []
    for sub in substitutes:
        days = []
        worked_hours = 0.0
        oncall_hours = 0.0
        num_shifts = 0
        ob_hours: dict[str, float] = {}
        current = start_date
        while current <= end_date:
            day = _build_substitute_day(current, sub, shift_map, shift_types, absence_by_date, ot_by_date)
            days.append(day)

            # Counting reads raw data, not the displayed shift, so that a day shown as OT
            # is still counted by its underlying scheduled shift (e.g. OC + OT same day).
            absence = absence_by_date.get((sub.id, current))
            entry = shift_map.get((sub.id, current))
            code = entry.shift_code if entry else None
            ot_entries_today = ot_by_date.get((sub.id, current), [])
            day_ot_hours = sum(o.hours or 0.0 for o in ot_entries_today)

            # Overtime counts as a worked pass too (in addition to any scheduled shift).
            num_shifts += len(ot_entries_today)

            # Per-day detail so the day renders like an agent day in the Excel export.
            day["ob_hours"] = {}
            day["ot_hours"] = day_ot_hours

            if absence is not None:
                # Absence days are counted via absence_counts below, not as worked shifts.
                pass
            elif code == "OC":
                # On-call standby is reduced by overtime worked during the on-call period.
                # Reuse the agent on-call calc for the per-code breakdown (hours only; no pay).
                if ot_entries_today:
                    _, oc_details = _recalculate_oncall_before_ot(current, ot_entries_today[0], {}, 0, settings, None)
                else:
                    oc_shift = next((s for s in shift_types if s.code == "OC"), None)
                    _, oc_details = _compute_oncall_pay(oc_shift, current, 0, {}, settings, None)
                day["oncall_details"] = oc_details
                oncall_hours += oc_details.get("total_hours", 0.0)
            elif code in ("N1", "N2", "N3"):
                hours, start, end = calculate_shift_hours(current, code)
                worked_hours += hours
                num_shifts += 1
                # OB hours for the worked shift, accumulated per OB code
                day_ob = calculate_ob_hours(start, end, combined_ob_rules)
                day["ob_hours"] = day_ob
                for ob_code, ob_h in day_ob.items():
                    if ob_h:
                        ob_hours[ob_code] = ob_hours.get(ob_code, 0.0) + ob_h
            current += datetime.timedelta(days=1)

        ot_hours = ot_hours_map.get(sub.id, 0.0)
        counts = absence_counts.get(sub.id, {})
        summaries.append(
            {
                "person_id": f"sub-{sub.id}",
                "substitute_id": sub.id,
                "person_name": sub.name,
                "days": days,
                "ob_pay": {},
                "ob_hours": ob_hours,
                "is_substitute": True,
                "num_shifts": num_shifts,
                "total_hours": worked_hours + ot_hours,
                "ot_hours": ot_hours,
                "ot_pay": 0.0,
                "oncall_hours": oncall_hours,
                "oncall_pay": 0.0,
                "sick_days": counts.get("SICK", 0),
                "vab_days": counts.get("VAB", 0),
                "leave_days": counts.get("LEAVE", 0),
                "off_days": counts.get("OFF", 0),
                "parental_days": counts.get("PARENTAL", 0),
                "vacation_days": counts.get("VACATION", 0),
                "brutto_pay": 0.0,
                "netto_pay": 0.0,
            }
        )
    return summaries


def _batch_fetch_absences(
    session,
    person_ids: list[int],
    start_date: datetime.date,
    end_date: datetime.date,
    rotation_to_user_id: dict[int, int] | None = None,
    extra_user_ids: set[int] | None = None,
) -> dict[tuple[int, datetime.date], object]:
    """
    Batch-hämtar frånvaro för flera personer och en period.

    Returns:
        Dict med (rotation_position, date) -> Absence
    """
    if not session:
        return {}

    from app.database.database import Absence

    # Resolve actual user_ids (may differ from rotation position)
    if rotation_to_user_id:
        user_ids = list({rotation_to_user_id.get(p, p) for p in person_ids})
        user_id_to_rotation = {v: k for k, v in rotation_to_user_id.items()}
    else:
        user_ids = person_ids
        user_id_to_rotation = {}

    # Include every user who held a viewed position at any point in the range, not
    # just its current holder, so a single-position view on the far side of a swap
    # or succession still fetches the prior/future holder's rows.
    if extra_user_ids:
        user_ids = list(set(user_ids) | set(extra_user_ids))

    absences = (
        session.query(Absence)
        .filter(
            Absence.user_id.in_(user_ids),
            Absence.date >= start_date,
            Absence.date <= end_date,
        )
        .all()
    )

    resolve_pos = _build_user_position_resolver(session, user_ids, user_id_to_rotation)
    return {(resolve_pos(a.user_id, a.date), a.date): a for a in absences}


def _batch_fetch_ot_shifts(
    session,
    person_ids: list[int],
    start_date: datetime.date,
    end_date: datetime.date,
    rotation_to_user_id: dict[int, int] | None = None,
    extra_user_ids: set[int] | None = None,
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

    # Include every user who held a viewed position at any point in the range, not
    # just its current holder, so a single-position view on the far side of a swap
    # or succession still fetches the prior/future holder's rows.
    if extra_user_ids:
        user_ids = list(set(user_ids) | set(extra_user_ids))

    # Also fetch the day before start_date to catch OT shifts crossing midnight
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

    resolve_pos = _build_user_position_resolver(session, user_ids, user_id_to_rotation)
    return {(resolve_pos(s.user_id, s.date), s.date): s for s in ot_shifts}


def _batch_fetch_oncall_overrides(
    session,
    person_ids: list[int],
    start_date: datetime.date,
    end_date: datetime.date,
    rotation_to_user_id: dict[int, int] | None = None,
    extra_user_ids: set[int] | None = None,
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

    # Include every user who held a viewed position at any point in the range, not
    # just its current holder, so a single-position view on the far side of a swap
    # or succession still fetches the prior/future holder's rows.
    if extra_user_ids:
        user_ids = list(set(user_ids) | set(extra_user_ids))

    overrides = (
        session.query(OnCallOverride)
        .filter(
            OnCallOverride.user_id.in_(user_ids),
            OnCallOverride.date >= start_date,
            OnCallOverride.date <= end_date,
        )
        .all()
    )

    resolve_pos = _build_user_position_resolver(session, user_ids, user_id_to_rotation)
    return {(resolve_pos(o.user_id, o.date), o.date): o for o in overrides}


def _batch_fetch_shift_overrides(
    session,
    person_ids: list[int],
    start_date: datetime.date,
    end_date: datetime.date,
    rotation_to_user_id: dict[int, int] | None = None,
    extra_user_ids: set[int] | None = None,
) -> dict[tuple[int, datetime.date], object]:
    """Batch-fetches manual shift overrides for multiple persons and a period.

    Returns:
        Dict med (rotation_position, date) -> ShiftOverride
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

    # Include every user who held a viewed position at any point in the range, not
    # just its current holder, so a single-position view on the far side of a swap
    # or succession still fetches the prior/future holder's rows.
    if extra_user_ids:
        user_ids = list(set(user_ids) | set(extra_user_ids))

    overrides = (
        session.query(ShiftOverride)
        .filter(
            ShiftOverride.user_id.in_(user_ids),
            ShiftOverride.date >= start_date,
            ShiftOverride.date <= end_date,
        )
        .all()
    )

    resolve_pos = _build_user_position_resolver(session, user_ids, user_id_to_rotation)
    return {(resolve_pos(o.user_id, o.date), o.date): o for o in overrides}


def _batch_fetch_day_pay_overrides(
    session,
    person_ids: list[int],
    start_date: datetime.date,
    end_date: datetime.date,
    rotation_to_user_id: dict[int, int] | None = None,
    extra_user_ids: set[int] | None = None,
) -> dict[tuple[int, datetime.date], object]:
    """Batch-fetches manual pay overrides (OB/oncall) for multiple persons and a period.

    Returns:
        Dict with (rotation_position, date) -> DayPayOverride
    """
    if not session:
        return {}

    from app.database.database import DayPayOverride

    if rotation_to_user_id:
        user_ids = list({rotation_to_user_id.get(p, p) for p in person_ids})
        user_id_to_rotation = {v: k for k, v in rotation_to_user_id.items()}
    else:
        user_ids = person_ids
        user_id_to_rotation = {}

    # Include every user who held a viewed position at any point in the range, not
    # just its current holder, so a single-position view on the far side of a swap
    # or succession still fetches the prior/future holder's rows.
    if extra_user_ids:
        user_ids = list(set(user_ids) | set(extra_user_ids))

    overrides = (
        session.query(DayPayOverride)
        .filter(
            DayPayOverride.user_id.in_(user_ids),
            DayPayOverride.date >= start_date,
            DayPayOverride.date <= end_date,
        )
        .all()
    )

    resolve_pos = _build_user_position_resolver(session, user_ids, user_id_to_rotation)
    return {(resolve_pos(o.user_id, o.date), o.date): o for o in overrides}


def _batch_fetch_swap_map(
    session,
    person_ids: list[int],
    start_date: datetime.date,
    end_date: datetime.date,
    rotation_to_user_id: dict[int, int] | None = None,
    extra_user_ids: set[int] | None = None,
) -> dict[tuple[int, datetime.date], str]:
    """
    Batch-hämtar accepterade skiftbyten för flera personer och en period.

    Returns str (shift_code), not an ORM object -- the value is computed
    by resolving which shift a person receives after a swap, derived from
    multiple ShiftSwap rows and determine_shift_for_date lookups.

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

    # Include every user who held a viewed position at any point in the range, not
    # just its current holder, so a single-position view on the far side of a swap
    # or succession still fetches the prior/future holder's rows.
    if extra_user_ids:
        user_ids = list(set(user_ids) | set(extra_user_ids))

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

    # Resolve each participant's rotation position by the specific date being keyed,
    # so a position change between requester_date and target_date buckets each shift
    # onto the position its holder actually occupied on that day.
    resolve_pos = _build_user_position_resolver(session, set(user_ids) | all_swap_user_ids, user_rotation)
    rotation_set = set(person_ids)
    swap_map = {}
    for swap in swaps:
        req_rot_rd = resolve_pos(swap.requester_id, swap.requester_date)
        tgt_rot_rd = resolve_pos(swap.target_id, swap.requester_date)
        req_rot_td = resolve_pos(swap.requester_id, swap.target_date)
        tgt_rot_td = resolve_pos(swap.target_id, swap.target_date)

        # On requester_date: they swap shifts
        if req_rot_rd in rotation_set:
            # Requester gets what target normally has on this date
            tgt_result = determine_shift_for_date(swap.requester_date, tgt_rot_rd)
            swap_map[(req_rot_rd, swap.requester_date)] = tgt_result[0].code if tgt_result and tgt_result[0] else "OFF"
        if tgt_rot_rd in rotation_set:
            # Target gets requester's shift on this date
            swap_map[(tgt_rot_rd, swap.requester_date)] = swap.requester_shift_code or "OFF"

        # On target_date: they swap shifts
        if tgt_rot_td in rotation_set:
            # Target gets what requester normally has on this date
            req_result = determine_shift_for_date(swap.target_date, req_rot_td)
            swap_map[(tgt_rot_td, swap.target_date)] = req_result[0].code if req_result and req_result[0] else "OFF"
        if req_rot_td in rotation_set:
            # Requester gets target's shift on this date
            swap_map[(req_rot_td, swap.target_date)] = swap.target_shift_code or "OFF"

    return swap_map


def _build_person_day_basic(
    date: datetime.date,
    person_id: int,
    ctx: DayLookupContext,
    session=None,
    employment_start: datetime.date | None = None,
) -> dict:
    """Builds basic day data for a person."""
    persons = ctx.persons
    shift_types = get_shift_types()
    vacation_dates = ctx.vacation_dates
    parental_dates = ctx.parental_dates
    absence_map = ctx.absence_map
    ot_shift_map = ctx.ot_shift_map
    oncall_override_map = ctx.oncall_override_map
    swap_map = ctx.swap_map
    shift_override_map = ctx.shift_override_map
    vacation_shift = get_vacation_shift()
    rotation_length = get_rotation_length_for_date(date)

    # Get person name via PersonHistory and whether the date precedes employment.
    person_name, show_off_before_employment = _resolve_day_person(session, person_id, date, persons, employment_start)

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

        # Partial absence: show original shift with truncated start/end
        if (
            absence.left_at is not None or absence.arrived_at is not None
        ) and absence.absence_type != AbsenceType.VACATION:
            result = determine_shift_for_date(date, person_id)
            if result and result[0]:
                original_shift, rotation_week = result
                hours, start, end = calculate_shift_hours(date, original_shift.code)
                if start is not None:
                    if absence.left_at:
                        left_time = datetime.datetime.strptime(absence.left_at, "%H:%M").time()
                        end = datetime.datetime.combine(date, left_time)
                        if end <= start:
                            end = start
                    if absence.arrived_at:
                        arrived_time = datetime.datetime.strptime(absence.arrived_at, "%H:%M").time()
                        start = datetime.datetime.combine(date, arrived_time)
                        if start >= end:
                            start = end
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
        # PARENTAL falls back to LEAVE shift since no dedicated shift type exists
        if absence.absence_type == AbsenceType.VACATION:
            absence_shift = vacation_shift
        elif absence.absence_type == AbsenceType.PARENTAL:
            absence_shift = next((s for s in shift_types if s.code == "LEAVE"), None)
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

    # Kolla semester (veckobaserad). Visa endast SEM på dagar personen är schemalagd
    # (icke-OFF), så markeringen matchar hur semesterdagar räknas. OFF-dagar lämnas
    # orörda och renderas som vanligt nedan. Dagsnivå-semester hanteras av absence-blocket ovan.
    if vacation_dates and vacation_shift and date in vacation_dates.get(person_id, set()):
        result = determine_shift_for_date(date, person_id)
        original_shift, rotation_week = result if result else (None, None)
        if original_shift and original_shift.code != "OFF":
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

    # Kolla föräldraledighet (veckobaserad). Samma regel: visa LEAVE endast på schemalagda
    # (icke-OFF) dagar. Dagsnivå-föräldraledighet hanteras av absence-blocket ovan.
    if parental_dates and date in parental_dates.get(person_id, set()):
        leave_shift = next((s for s in shift_types if s.code == "LEAVE"), None)
        result = determine_shift_for_date(date, person_id)
        original_shift, rotation_week = result if result else (None, None)
        if leave_shift and original_shift and original_shift.code != "OFF":
            return {
                "person_id": person_id,
                "person_name": person_name,
                "shift": leave_shift,
                "original_shift": original_shift,
                "rotation_week": rotation_week,
                "rotation_length": rotation_length,
                "hours": 0.0,
                "start": None,
                "end": None,
                # Week-based parental leave renders as LEAVE; flag it so summaries can
                # count it as parental rather than ordinary leave.
                "parental_leave": True,
            }

    # Check for manual shift override (admin-assigned N1/N2/N3 replacing rotation)
    if shift_override_map is not None:
        _shift_override_obj = shift_override_map.get((person_id, date))
        override_code = _shift_override_obj.shift_code if _shift_override_obj else None
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
            # Add on-call shift, replacing the regular shift
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


def _resolve_day_person(
    session,
    person_id: int,
    current_day: datetime.date,
    persons,
    employment_start: datetime.date | None,
) -> tuple[str, bool]:
    """Resolve (person_name, show_off_before_employment) for a position on a date.

    Uses PersonHistory to find who held the position on the date, falling back to the current
    holder, and flags before-employment when the date precedes their start (or the viewer's).
    """
    person_name = persons[person_id - 1].name  # Default fallback
    show_off_before_employment = False

    if session:
        # Position vacated with no successor: render OFF after the last employment ended
        vacancy = get_position_vacancy(session, person_id, current_day)
        if vacancy:
            return vacancy.name, True

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

    return person_name, show_off_before_employment


def _populate_absence_day(
    day_info: dict,
    absence,
    current_day: datetime.date,
    person_id: int,
    person_name: str,
    combined_ob_rules,
    vacation_shift,
    shift_types,
) -> bool:
    """Fill day_info for a day with an absence. Returns True when handled (caller stops).

    Returns False when the absence does not resolve to a shift (e.g. a partial absence on a
    day with no scheduled shift, or an unknown absence type), so the caller continues.
    """
    from app.database.database import AbsenceType

    # Partial absence: calculate OB and hours for the worked portion of the shift
    if (absence.left_at is not None or absence.arrived_at is not None) and absence.absence_type != AbsenceType.VACATION:
        result = determine_shift_for_date(current_day, person_id)
        if result and result[0]:
            original_shift, rotation_week = result
            hours, start, end = calculate_shift_hours(current_day, original_shift.code)
            if start is not None:
                if absence.left_at:
                    left_time = datetime.datetime.strptime(absence.left_at, "%H:%M").time()
                    end = datetime.datetime.combine(current_day, left_time)
                    if end <= start:
                        end = start
                if absence.arrived_at:
                    arrived_time = datetime.datetime.strptime(absence.arrived_at, "%H:%M").time()
                    start = datetime.datetime.combine(current_day, arrived_time)
                    if start >= end:
                        start = end
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
            return True

    # VACATION absences use the SEM shift (same as week-based vacation)
    # PARENTAL falls back to LEAVE shift since no dedicated shift type exists
    if absence.absence_type == AbsenceType.VACATION:
        absence_shift = vacation_shift
    elif absence.absence_type == AbsenceType.PARENTAL:
        absence_shift = next((s for s in shift_types if s.code == "LEAVE"), None)
    else:
        absence_shift = next((s for s in shift_types if s.code == absence.absence_type.value), None)
    if absence_shift:
        # Get original shift for coworker matching
        result = determine_shift_for_date(current_day, person_id)
        original_shift, rotation_week = result if result else (None, None)
        day_info.update(
            {
                "person_id": person_id,
                "person_name": person_name,
                "shift": absence_shift,
                "original_shift": original_shift,  # For coworker matching
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
            }
        )
        return True

    return False


def _populate_parental_day(
    day_info: dict,
    current_day: datetime.date,
    person_id: int,
    person_name: str,
    parental_dates,
    shift_types,
) -> bool:
    """Fill day_info for a week-based parental-leave day (LEAVE shift). Returns True when handled."""
    if not (parental_dates and current_day in parental_dates.get(person_id, set())):
        return False
    leave_shift = next((s for s in shift_types if s.code == "LEAVE"), None)
    if not leave_shift:
        return False
    result = determine_shift_for_date(current_day, person_id)
    original_shift, rotation_week = result if result else (None, None)
    # Week-based parental leave only marks scheduled (non-OFF) days; OFF days stay OFF.
    if not (original_shift and original_shift.code != "OFF"):
        return False
    day_info.update(
        {
            "person_id": person_id,
            "person_name": person_name,
            "shift": leave_shift,
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
            # Week-based parental leave renders as LEAVE; flag it so summaries can
            # count it as parental rather than ordinary leave.
            "parental_leave": True,
        }
    )
    return True


class _ShiftResolution(NamedTuple):
    shift: object
    rotation_week: object
    hours: float
    start: object
    end: object
    ob: dict


def _resolve_effective_shift(
    current_day: datetime.date,
    person_id: int,
    vacation_shift,
    vacation_dates,
    shift_override_map,
    swap_map,
    shift_types,
    combined_ob_rules,
) -> _ShiftResolution:
    """Resolve the effective shift for a day, in priority order.

    Vacation (SEM) > manual shift override > accepted shift swap > the rotation shift. Returns
    the shift plus its hours/start/end and OB hours (OC shifts and missing shifts carry no OB).
    """

    def _with_ob(shift, rotation_week) -> _ShiftResolution:
        if shift is None:
            return _ShiftResolution(None, None, 0.0, None, None, {})
        hours, start, end = calculate_shift_hours(current_day, shift.code)
        ob = calculate_ob_hours(start, end, combined_ob_rules) if (start is not None and shift.code != "OC") else {}
        return _ShiftResolution(shift, rotation_week, hours, start, end, ob)

    # Vacation (week-based): only mark scheduled (non-OFF) days; OFF days fall through
    # to the rotation shift below so they render as OFF.
    if vacation_shift and current_day in vacation_dates.get(person_id, set()):
        rot = determine_shift_for_date(current_day, person_id)
        rot_shift = rot[0] if rot else None
        if rot_shift and rot_shift.code != "OFF":
            return _ShiftResolution(vacation_shift, None, 0.0, None, None, {})

    # Manual shift override
    if shift_override_map is not None and shift_override_map.get((person_id, current_day)):
        override_code = shift_override_map[(person_id, current_day)].shift_code
        result = determine_shift_for_date(current_day, person_id)
        rotation_week = result[1] if result else None
        override_shift = next((s for s in shift_types if s.code == override_code), None)
        return _with_ob(override_shift, rotation_week if override_shift else None)

    # Accepted shift swap
    if swap_map is not None and (person_id, current_day) in swap_map:
        new_code = swap_map[(person_id, current_day)]
        result = determine_shift_for_date(current_day, person_id)
        rotation_week = result[1] if result else None
        swapped_shift = next((s for s in shift_types if s.code == new_code), None)
        return _with_ob(swapped_shift, rotation_week if swapped_shift else None)

    # Rotation shift
    result = determine_shift_for_date(current_day, person_id)
    if result is None or result[0] is None:
        return _ShiftResolution(None, None, 0.0, None, None, {})
    return _with_ob(result[0], result[1])


def _compute_oncall_pay(
    shift, current_day, person_id, user_wages, settings, oncall_rate_override
) -> tuple[float, dict]:
    """On-call pay for a day. Returns (pay, details); (0.0, {}) for non-OC shifts."""
    if not (shift and shift.code == "OC"):
        return 0.0, {}
    oncall_rules = get_oncall_rules(current_day.year)
    oncall_calc = calculate_oncall_pay(
        current_day,
        user_wages.get(person_id, settings.monthly_salary),
        oncall_rules,
        rate_overrides=oncall_rate_override,
    )
    return oncall_calc["total_pay"], oncall_calc


def _compute_overtime_pay(
    ot_shift, current_day, person_id, session, settings, ot_rate_override
) -> tuple[float, float, dict]:
    """Overtime pay/hours/details for an OT shift, using the historical wage for the date."""
    from .wages import get_ot_hourly_rate_from_stored_wage, get_user_wage

    wage_for_date = get_user_wage(session, person_id, settings.monthly_salary, effective_date=current_day)
    hourly_rate = (
        float(ot_rate_override)
        if ot_rate_override is not None
        else get_ot_hourly_rate_from_stored_wage(session, person_id, wage_for_date)
    )
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
    return ot_pay, ot_hours, ot_details


def _apply_oncall_override(override, shift, hours, start, end, ob, shift_types):
    """Apply a manual on-call override to the day's shift.

    ADD replaces the shift with OC; REMOVE turns an OC shift into OFF. Returns the (possibly
    modified) (shift, hours, start, end, ob); unchanged when there is no override.
    """
    if override is None:
        return shift, hours, start, end, ob

    from app.database.database import OnCallOverrideType

    if override.override_type == OnCallOverrideType.ADD:
        oc_shift = next((s for s in shift_types if s.code == "OC"), None)
        if oc_shift:
            return oc_shift, 0.0, None, None, {}  # OC har inga specifika tider
    elif override.override_type == OnCallOverrideType.REMOVE:
        if shift and shift.code == "OC":
            off_shift = next((s for s in shift_types if s.code == "OFF"), None)
            if off_shift:
                return off_shift, 0.0, None, None, {}

    return shift, hours, start, end, ob


def _populate_single_person_day(
    day_info: dict,
    current_day: datetime.date,
    person_id: int,
    ctx: DayLookupContext,
    session,
    employment_start: datetime.date | None = None,
) -> None:
    """Populates detailed day info for a person."""
    vacation_dates = ctx.vacation_dates
    combined_ob_rules = ctx.combined_ob_rules
    user_wages = ctx.user_wages
    persons = ctx.persons
    settings = ctx.settings
    ot_shift_map = ctx.ot_shift_map
    absence_map = ctx.absence_map
    oncall_override_map = ctx.oncall_override_map
    swap_map = ctx.swap_map
    user_rates_map = ctx.user_rates_map
    shift_override_map = ctx.shift_override_map
    parental_dates = ctx.parental_dates
    vacation_shift = get_vacation_shift()
    shift_types = get_shift_types()

    # Get person name via PersonHistory (shows correct person for this specific date)
    person_name, show_off_before_employment = _resolve_day_person(
        session, person_id, current_day, persons, employment_start
    )

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

    if absence and _populate_absence_day(
        day_info, absence, current_day, person_id, person_name, combined_ob_rules, vacation_shift, shift_types
    ):
        return

    # Kolla föräldraledighet (veckobaserad + dagsnivå via parental_dates)
    if _populate_parental_day(day_info, current_day, person_id, person_name, parental_dates, shift_types):
        return

    # Kolla semester / override / byte / normalt skift (i prioritetsordning)
    shift, rotation_week, hours, start, end, ob = _resolve_effective_shift(
        current_day,
        person_id,
        vacation_shift,
        vacation_dates,
        shift_override_map,
        swap_map,
        shift_types,
        combined_ob_rules,
    )

    # Rotationsskiftet (för coworker-matchning och "visa rotation"-toggle i alla-vyer)
    _rot = determine_shift_for_date(current_day, person_id)
    original_shift = _rot[0] if _rot else shift

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

    shift, hours, start, end, ob = _apply_oncall_override(oncall_override, shift, hours, start, end, ob, shift_types)

    # Calculate on-call pay
    _person_rates = (user_rates_map or {}).get(person_id) or {}
    oncall_pay, oncall_details = _compute_oncall_pay(
        shift, current_day, person_id, user_wages, settings, _person_rates.get("oncall")
    )

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
        ot_pay, ot_hours, ot_details = _compute_overtime_pay(
            ot_shift, current_day, person_id, session, settings, _person_rates.get("ot")
        )

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

    # Apply manual hour overrides if one exists for this person and date
    day_pay_override_map = ctx.day_pay_override_map or {}
    day_pay_override = day_pay_override_map.get((person_id, current_day))
    ob_hours_override = None
    if day_pay_override:
        if day_pay_override.oncall_hours_override:
            from app.core.oncall import _cached_oncall_rules as _get_oc_rules
            from app.core.oncall import apply_oncall_hours_override

            _oc_rules = _get_oc_rules(current_day.year)
            _person_oc_rates = (_person_rates or {}).get("oncall")
            oncall_pay, oncall_details = apply_oncall_hours_override(
                day_pay_override.oncall_hours_override,
                oncall_details.get("breakdown", {}),
                user_wages.get(person_id, settings.monthly_salary),
                _oc_rules,
                _person_oc_rates,
            )
        if day_pay_override.ob_hours_override:
            ob_hours_override = day_pay_override.ob_hours_override

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
            "ob_hours_override": ob_hours_override,
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
    """Recalculates on-call pay for the period before AND after overtime.

    On-call is paid for 24h minus overtime hours.
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
