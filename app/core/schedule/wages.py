"""Lönehantering från databas."""

import datetime
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.core.constants import PERSON_IDS
from app.core.utils import get_today


def _get_user_id_for_position(session, person_id: int) -> int:
    """
    Get the user_id for the current holder of a rotation position.

    Args:
        session: SQLAlchemy session
        person_id: Rotation position (1-10)

    Returns:
        user_id of the current holder, or person_id as fallback (legacy behavior)
    """
    from app.database.database import User

    # First check if someone has this person_id explicitly set
    holder = session.query(User).filter(User.person_id == person_id).first()
    if holder:
        return holder.id

    # Fallback: legacy behavior where user_id == person_id
    return person_id


def get_user_wage(session, user_id: int, fallback: int | None = None, effective_date: date | None = None) -> int:
    """
    Hämtar en användares lön från databasen med temporal validity support.

    Args:
        session: SQLAlchemy session
        user_id: Användar-ID
        fallback: Fallback-värde om användare saknas
        effective_date: Datum för vilket lönen ska hämtas (None = dagens lön)

    Returns:
        Lön i SEK som var giltig på angivet datum
    """
    from .core import get_settings

    settings = get_settings()
    default = fallback or settings.monthly_salary

    if not session:
        return default

    # If no effective_date specified, use current wage from User table
    if effective_date is None:
        from app.database.database import User

        user = session.query(User).filter(User.id == user_id).first()
        return user.wage if user else default

    # Query wage history for the specific date
    from app.database.database import WageHistory

    wage_record = (
        session.query(WageHistory)
        .filter(
            WageHistory.user_id == user_id,
            WageHistory.effective_from <= effective_date,
            # Either no end date (current wage) OR end date is after effective_date
            (WageHistory.effective_to.is_(None)) | (WageHistory.effective_to > effective_date),
        )
        .order_by(WageHistory.effective_from.desc())
        .first()
    )

    if wage_record:
        return wage_record.wage

    # Fallback: try to get current wage from User table
    from app.database.database import User

    user = session.query(User).filter(User.id == user_id).first()
    return user.wage if user else default


def get_all_user_wages(session) -> dict[int, int]:
    """
    Hämtar alla användares löner i en query.

    Mer effektivt för batch-operationer än att anropa get_user_wage() i loop.

    Returns:
        Dict med user_id -> lön
    """
    from app.core.storage import load_persons

    from .core import get_settings

    settings = get_settings()

    if not session:
        persons = load_persons()
        return {p.id: p.wage for p in persons}

    from app.database.database import User

    users = session.query(User).filter(User.id.in_(PERSON_IDS)).all()
    wages = {user.id: user.wage for user in users}

    # Fyll i saknade med fallback
    for pid in PERSON_IDS:
        if pid not in wages:
            wages[pid] = settings.monthly_salary

    return wages


KARENS_HOURS = 8.0  # Total karensbudget per sjukperiod (= 20% av normal arbetsvecka)


def calculate_absence_deduction(
    monthly_wage: int,
    absence_type: str,
    shift_hours: float = 8.5,
    is_first_sick_day: bool = False,
    absent_hours: float | None = None,
    karens_remaining: float | None = None,
) -> float:
    """
    Beräknar löneavdrag för frånvaro baserat på timmar.

    Args:
        monthly_wage: Månadslön i SEK
        absence_type: Typ av frånvaro (SICK, VAB, LEAVE, OFF)
        shift_hours: Antal timmar för skiftet (default 8.5), används om absent_hours saknas
        is_first_sick_day: Om det är första sjukdagen (används om karens_remaining saknas)
        absent_hours: Faktiska frånvarotimmar vid partiell dag (None = heldag = shift_hours)
        karens_remaining: Återstående karenstimmar i sjukperioden (None = beräkna från is_first_sick_day)

    Returns:
        Avdrag i SEK

    Regler:
        - SICK: karensbudget = 8h per sjukperiod, fördelas dag för dag tills slut
          Varje dag: karens_idag = min(frånvarotimmar, karens_kvar)
                     sjuklön_idag = frånvarotimmar - karens_idag (20% avdrag)
        - VAB: 100% avdrag (FK betalar ersättning, inte arbetsgivaren)
        - LEAVE: 100% avdrag (obetald ledighet)
        - OFF: 0% avdrag (betald ledighet)
    """
    hourly_wage = monthly_wage / 173.33
    hours = absent_hours if absent_hours is not None else shift_hours

    if absence_type == "SICK":
        if karens_remaining is not None:
            # Distribuerad karens: förbruka budget tills den är slut
            karens_today = min(hours, karens_remaining)
            sjuklon_hours = hours - karens_today
            return hourly_wage * karens_today + hourly_wage * sjuklon_hours * 0.2
        elif is_first_sick_day:
            # Fallback (äldre anrop): dag 1 = hela karensbudgeten eller frånvarotimmar
            karens_h = hours if absent_hours is not None else KARENS_HOURS
            return hourly_wage * karens_h
        else:
            return hourly_wage * hours * 0.2
    elif absence_type == "VAB":
        return hourly_wage * hours
    elif absence_type == "LEAVE":
        return hourly_wage * hours
    elif absence_type == "OFF":
        return 0.0
    else:
        return 0.0


def get_karens_consumed_before_date(session, user_id: int, sick_date: "date") -> float:
    """
    Beräknar hur många karenstimmar som redan förbrukats i den pågående sjukperioden
    INNAN sick_date.

    En sjukperiod bryts om det är mer än 5 dagars uppehåll mellan sjukdagar.

    Returns:
        Förbrukade karenstimmar (0.0 om sick_date är första dagen i perioden)
    """
    from app.database.database import Absence, AbsenceType

    # Hämta alla sjukdagar bakåt från sick_date (upp till 30 dagar bakåt är tillräckligt)
    lookback = sick_date - timedelta(days=30)
    prev_sick_days = (
        session.query(Absence)
        .filter(
            Absence.user_id == user_id,
            Absence.absence_type == AbsenceType.SICK,
            Absence.date >= lookback,
            Absence.date < sick_date,
        )
        .order_by(Absence.date.desc())
        .all()
    )

    if not prev_sick_days:
        return 0.0

    # Gå bakåt och samla dagar i samma sjukperiod (gap <= 5 dagar)
    period_days: list = []
    prev_date = sick_date
    for absence in prev_sick_days:
        gap = (prev_date - absence.date).days
        if gap > 5:
            break  # Ny sjukperiod - sluta leta
        period_days.append(absence)
        prev_date = absence.date

    if not period_days:
        return 0.0

    # Räkna förbrukad karens (kronologisk ordning)
    period_days.reverse()
    consumed = 0.0
    for absence in period_days:
        if consumed >= KARENS_HOURS:
            break
        # Hämta frånvarotimmar för denna dag
        _, _, shift_end_dt = get_shift_times_for_date(session, user_id, absence.date)
        shift_h = get_shift_hours_for_date(session, user_id, absence.date)
        absent_h = get_absent_hours_from_left_at(absence.left_at, shift_end_dt, shift_h)
        karens_this_day = min(absent_h, KARENS_HOURS - consumed)
        consumed += karens_this_day

    return consumed


def _get_rotation_position(session, user_id: int) -> int:
    """Hämtar rotation_person_id för en användare (fallback = user_id)."""
    if session:
        from app.database.database import User

        user = session.query(User).filter(User.id == user_id).first()
        if user:
            return user.rotation_person_id
    return user_id


def get_shift_times_for_date(
    session, user_id: int, absence_date: date
) -> tuple[float, datetime.datetime | None, datetime.datetime | None]:
    """
    Hämtar (hours, start_dt, end_dt) för skiftet en person skulle ha jobbat.
    Returnerar (8.5, None, None) som fallback.
    """
    from app.core.schedule import calculate_shift_hours, determine_shift_for_date

    rotation_position = _get_rotation_position(session, user_id)
    result = determine_shift_for_date(absence_date, start_week=rotation_position)
    if result and result[0]:
        shift, _ = result
        if shift and shift.code not in ["OFF", "SEM"]:
            hours, start_dt, end_dt = calculate_shift_hours(absence_date, shift.code)
            if hours > 0:
                return hours, start_dt, end_dt
    return 8.5, None, None


def get_shift_hours_for_date(session, user_id: int, absence_date: date) -> float:
    """
    Hämtar antal timmar för det skift som skulle ha jobbats på given dag.
    Default 8.5 om inget skift hittas.
    """
    hours, _, _ = get_shift_times_for_date(session, user_id, absence_date)
    return hours


def get_absent_hours_from_left_at(left_at: str, shift_end_dt: datetime.datetime | None, shift_hours: float) -> float:
    """
    Beräknar antal frånvarotimmar utifrån left_at ("HH:MM") och skiftets sluttid.
    Returnerar shift_hours som fallback om sluttid saknas.
    """
    if not left_at or shift_end_dt is None:
        return shift_hours
    try:
        left_time = datetime.datetime.strptime(left_at, "%H:%M").time()
        left_dt = datetime.datetime.combine(shift_end_dt.date(), left_time)
        # Hantera fall där left_at är nästa dag (t.ex. nattskift)
        if left_dt >= shift_end_dt:
            return 0.0
        absent = (shift_end_dt - left_dt).total_seconds() / 3600.0
        return max(0.0, absent)
    except ValueError:
        return shift_hours


def get_absence_deductions_for_month(session: Session, user_id: int, year: int, month: int, monthly_wage: int) -> dict:
    """
    Beräknar totala löneavdrag för frånvaro under en månad.

    Args:
        session: SQLAlchemy session
        user_id: Användar-ID
        year: År
        month: Månad
        monthly_wage: Månadslön i SEK

    Returns:
        Dict med:
            - total_deduction: Totalt avdrag i SEK
            - total_hours: Totalt antal frånvarotimmar
            - sick_days: Antal sjukdagar
            - sick_hours: Antal sjuktimmar
            - vab_days: Antal VAB-dagar
            - vab_hours: Antal VAB-timmar
            - leave_days: Antal lediga dagar
            - leave_hours: Antal lediga timmar
            - details: Lista med detaljer per dag
    """
    from datetime import timedelta

    from app.database.database import Absence, AbsenceType

    # Hämta alla frånvaror för månaden
    start_date = date(year, month, 1)
    if month == 12:
        end_date = date(year, 12, 31)
    else:
        end_date = date(year, month + 1, 1) - timedelta(days=1)

    absences = (
        session.query(Absence)
        .filter(Absence.user_id == user_id, Absence.date >= start_date, Absence.date <= end_date)
        .order_by(Absence.date)
        .all()
    )

    total_deduction = 0.0
    total_hours = 0.0
    sick_days = 0
    sick_hours = 0.0
    vab_days = 0
    vab_hours = 0.0
    leave_days = 0
    leave_hours = 0.0
    off_days = 0
    off_hours = 0.0
    details = []

    # Spåra karensbudget och sjukperiod
    last_sick_date: date | None = None
    karens_consumed_in_period = 0.0

    for absence in absences:
        # Hämta antal timmar för det skift som skulle ha jobbats
        shift_hours = get_shift_hours_for_date(session, user_id, absence.date)
        _, _, shift_end_dt = get_shift_times_for_date(session, user_id, absence.date)
        absent_hours = get_absent_hours_from_left_at(absence.left_at, shift_end_dt, shift_hours)
        total_hours += absent_hours

        if absence.absence_type == AbsenceType.SICK:
            sick_days += 1
            sick_hours += absent_hours

            # Ny sjukperiod om gap > 5 dagar
            if last_sick_date is None or (absence.date - last_sick_date).days > 5:
                karens_consumed_in_period = 0.0

            karens_remaining = max(0.0, KARENS_HOURS - karens_consumed_in_period)
            karens_today = min(absent_hours, karens_remaining)
            karens_consumed_in_period += karens_today
            last_sick_date = absence.date

            deduction = calculate_absence_deduction(
                monthly_wage,
                absence.absence_type.value,
                shift_hours,
                absent_hours=absent_hours,
                karens_remaining=karens_remaining,
            )
            is_karens = karens_today > 0

        else:
            karens_today = 0.0
            is_karens = False
            if absence.absence_type == AbsenceType.VAB:
                vab_days += 1
                vab_hours += absent_hours
            elif absence.absence_type == AbsenceType.LEAVE:
                leave_days += 1
                leave_hours += absent_hours
            elif absence.absence_type == AbsenceType.OFF:
                off_days += 1
                off_hours += absent_hours

            deduction = calculate_absence_deduction(
                monthly_wage, absence.absence_type.value, shift_hours, absent_hours=absent_hours
            )

        total_deduction += deduction

        details.append(
            {
                "date": absence.date,
                "type": absence.absence_type.value,
                "hours": absent_hours,
                "deduction": deduction,
                "is_karens": is_karens,
                "karens_hours": karens_today,
                "is_partial": absence.left_at is not None,
                "left_at": absence.left_at,
            }
        )

    return {
        "total_deduction": total_deduction,
        "total_hours": total_hours,
        "sick_days": sick_days,
        "sick_hours": sick_hours,
        "vab_days": vab_days,
        "vab_hours": vab_hours,
        "leave_days": leave_days,
        "leave_hours": leave_hours,
        "off_days": off_days,
        "off_hours": off_hours,
        "details": details,
    }


# ============================================================================
# Wage History Management Functions
# ============================================================================


def add_new_wage(session: Session, user_id: int, new_wage: int, effective_from: date, created_by: int | None = None):
    """
    Lägger till en ny lön för en användare med angiven effective_from date.

    Denna funktion:
    1. Sätter effective_to på nuvarande lön (om den finns)
    2. Skapar ny lönepost med effective_from
    3. Uppdaterar User.wage för snabb access till nuvarande lön

    Args:
        session: SQLAlchemy session
        user_id: Användar-ID
        new_wage: Ny lön i SEK
        effective_from: Datum när nya lönen börjar gälla
        created_by: Användar-ID för den som skapar ändringen

    Returns:
        Den skapade WageHistory-posten
    """
    from datetime import timedelta

    from app.database.database import User, WageHistory

    # Close previous wage history (set effective_to to day before new wage starts)
    previous_wage = (
        session.query(WageHistory).filter(WageHistory.user_id == user_id, WageHistory.effective_to.is_(None)).first()
    )

    if previous_wage:
        # Set end date to day before new wage starts
        previous_wage.effective_to = effective_from - timedelta(days=1)

    # Create new wage history entry
    new_wage_history = WageHistory(
        user_id=user_id,
        wage=new_wage,
        effective_from=effective_from,
        effective_to=None,  # NULL = current/future wage
        created_by=created_by,
    )

    session.add(new_wage_history)

    # Update User.wage for current wage (for backwards compatibility and performance)
    # Only update if this is the current or future wage
    if effective_from <= get_today():
        user = session.query(User).filter(User.id == user_id).first()
        if user:
            user.wage = new_wage

    session.commit()

    return new_wage_history


def get_wage_history(session: Session, user_id: int) -> list[dict]:
    """
    Hämtar all lönehistorik för en användare, sorterad efter datum.

    Args:
        session: SQLAlchemy session
        user_id: Användar-ID

    Returns:
        Lista med lönehistorik (nyaste först)
    """
    from app.database.database import WageHistory

    wage_records = (
        session.query(WageHistory)
        .filter(WageHistory.user_id == user_id)
        .order_by(WageHistory.effective_from.desc())
        .all()
    )

    return [
        {
            "id": record.id,
            "wage": record.wage,
            "effective_from": record.effective_from,
            "effective_to": record.effective_to,
            "is_current": record.effective_to is None,
            "created_at": record.created_at,
        }
        for record in wage_records
    ]


def get_current_wage_record(session: Session, user_id: int):
    """
    Hämtar den nuvarande löneposten för en användare.

    Args:
        session: SQLAlchemy session
        user_id: Användar-ID

    Returns:
        WageHistory-post eller None
    """
    from app.database.database import WageHistory

    return session.query(WageHistory).filter(WageHistory.user_id == user_id, WageHistory.effective_to.is_(None)).first()
