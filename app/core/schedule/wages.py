"""Lönehantering från databas."""

from datetime import date

from sqlalchemy.orm import Session

from app.core.constants import PERSON_IDS


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


def calculate_absence_deduction(
    monthly_wage: int, absence_type: str, shift_hours: float = 8.5, is_first_sick_day: bool = False
) -> float:
    """
    Beräknar löneavdrag för frånvaro baserat på timmar.

    Args:
        monthly_wage: Månadslön i SEK
        absence_type: Typ av frånvaro (SICK, VAB, LEAVE, OFF)
        shift_hours: Antal timmar för skiftet (default 8.5)
        is_first_sick_day: Om det är första sjukdagen (karensdag)

    Returns:
        Avdrag i SEK

    Regler:
        - SICK: Första dagen (karensdag) = 100% avdrag, därefter 20% avdrag (80% sjuklön från arbetsgivaren)
        - VAB: 100% avdrag (ersättning kommer från Försäkringskassan, inte arbetsgivaren)
        - LEAVE: 100% avdrag (obetald ledighet)
        - OFF: 0% avdrag (betald ledighet)
    """
    # Beräkna timlön (månadslön / 173.33 timmar per månad enligt svensk standard)
    hourly_wage = monthly_wage / 173.33

    # Beräkna lön för skiftet
    shift_wage = hourly_wage * shift_hours

    if absence_type == "SICK":
        if is_first_sick_day:
            # Karensdag - 100% avdrag
            return shift_wage
        else:
            # Sjuklön - 20% avdrag (arbetsgivaren betalar 80%)
            return shift_wage * 0.2
    elif absence_type == "VAB":
        # VAB - 100% avdrag (FK betalar ersättning, inte arbetsgivaren)
        return shift_wage
    elif absence_type == "LEAVE":
        # Obetald ledighet - 100% avdrag
        return shift_wage
    elif absence_type == "OFF":
        # Ledig - inget löneavdrag (betald ledighet)
        return 0.0
    else:
        # Okänd frånvarotyp - inget avdrag
        return 0.0


def get_shift_hours_for_date(session: Session, user_id: int, absence_date: date) -> float:
    """
    Hämtar antal timmar för det skift som skulle ha jobbats på given dag.

    Args:
        session: SQLAlchemy session
        user_id: Användar-ID
        absence_date: Datum för frånvaron

    Returns:
        Antal timmar för skiftet (default 8.5 om inget skift hittas)
    """
    from app.core.schedule import calculate_shift_hours, determine_shift_for_date

    # Hämta vilket skift personen skulle ha jobbat
    result = determine_shift_for_date(absence_date, start_week=user_id)

    if result and result[0]:
        shift, _ = result
        if shift and shift.code not in ["OFF", "SEM"]:
            hours, _, _ = calculate_shift_hours(absence_date, shift.code)
            return hours if hours > 0 else 8.5

    # Default till 8.5 timmar om vi inte kan bestämma skiftet
    return 8.5


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

    # Håll koll på om vi har haft karensdag för sjukperiod
    last_sick_date = None

    for absence in absences:
        is_first_sick_day = False

        # Hämta antal timmar för det skift som skulle ha jobbats
        shift_hours = get_shift_hours_for_date(session, user_id, absence.date)
        total_hours += shift_hours

        if absence.absence_type == AbsenceType.SICK:
            sick_days += 1
            sick_hours += shift_hours

            # Kolla om det är en ny sjukperiod (mer än 5 dagar sedan senaste sjukdag)
            if last_sick_date is None or (absence.date - last_sick_date).days > 5:
                is_first_sick_day = True

            last_sick_date = absence.date

        elif absence.absence_type == AbsenceType.VAB:
            vab_days += 1
            vab_hours += shift_hours
        elif absence.absence_type == AbsenceType.LEAVE:
            leave_days += 1
            leave_hours += shift_hours
        elif absence.absence_type == AbsenceType.OFF:
            off_days += 1
            off_hours += shift_hours

        deduction = calculate_absence_deduction(
            monthly_wage, absence.absence_type.value, shift_hours, is_first_sick_day
        )

        total_deduction += deduction

        details.append(
            {
                "date": absence.date,
                "type": absence.absence_type.value,
                "hours": shift_hours,
                "deduction": deduction,
                "is_karens": is_first_sick_day,
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
    if effective_from <= date.today():
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
