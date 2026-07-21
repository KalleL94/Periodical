"""Wage calculations from the database."""

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


_MONTHLY_HOURS = 173.33  # Standard Swedish monthly hours divisor


def _get_wage_type(session, user_id: int):
    """Returns the WageType for a user. Defaults to MONTHLY if not set."""
    from app.database.database import User, WageType

    if not session:
        return WageType.MONTHLY
    user = session.query(User).filter(User.id == user_id).first()
    if user and user.wage_type:
        return user.wage_type
    return WageType.MONTHLY


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
            # Either no end date (current wage) OR end date is on/after effective_date.
            # effective_to is set by add_new_wage() as an INCLUSIVE last valid day
            # (new_effective_from - 1 day), so this must be >= to cover that boundary day.
            (WageHistory.effective_to.is_(None)) | (WageHistory.effective_to >= effective_date),
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


def get_effective_monthly_wage(
    session, user_id: int, fallback: int | None = None, effective_date: date | None = None
) -> int:
    """
    Returns a monthly-equivalent wage for use in calculations that divide by 173.33.

    For MONTHLY workers: returns stored wage as-is.
    For HOURLY workers: returns int(hourly_rate * 173.33) so that
      effective_monthly / 173.33 == hourly_rate.
    """
    from app.database.database import WageType

    wage = get_user_wage(session, user_id, fallback, effective_date)
    wage_type = _get_wage_type(session, user_id)
    if wage_type == WageType.HOURLY:
        return int(wage * _MONTHLY_HOURS)
    return wage


def get_ot_hourly_rate_from_stored_wage(session, user_id: int, stored_wage: int) -> float:
    """
    Returns the OT hourly rate given the stored wage value.

    For MONTHLY workers: stored_wage / OT_RATE_DIVISOR (72).
    For HOURLY workers: stored_wage directly (it IS already the hourly rate).
    """
    from app.core.constants import OT_RATE_DIVISOR
    from app.database.database import WageType

    wage_type = _get_wage_type(session, user_id)
    if wage_type == WageType.HOURLY:
        return float(stored_wage)
    return stored_wage / OT_RATE_DIVISOR


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

    from app.database.database import User, WageType

    users = session.query(User).filter(User.id.in_(PERSON_IDS)).all()
    wages = {}
    for user in users:
        if user.wage_type == WageType.HOURLY:
            wages[user.id] = int(user.wage * _MONTHLY_HOURS)
        else:
            wages[user.id] = user.wage

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
        absence_type: Typ av frånvaro (SICK, VAB, LEAVE, OFF, PARENTAL)
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
    hourly_wage = monthly_wage / _MONTHLY_HOURS
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
    elif absence_type == "PARENTAL":
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

    # Look back up to 30 days to find prior sick days in the same sick period
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

    # Walk backward collecting days in the same sick period (gap <= 5 days)
    period_days: list = []
    prev_date = sick_date
    for absence in prev_sick_days:
        gap = (prev_date - absence.date).days
        if gap > 5:
            break  # New sick period, stop looking back
        period_days.append(absence)
        prev_date = absence.date

    if not period_days:
        return 0.0

    # Accumulate consumed waiting-day hours in chronological order
    period_days.reverse()
    consumed = 0.0
    for absence in period_days:
        if consumed >= KARENS_HOURS:
            break
        shift_h, shift_start_dt, shift_end_dt = get_shift_times_for_date(session, user_id, absence.date)
        absent_h = get_absent_hours_for_absence(absence, shift_start_dt, shift_end_dt, shift_h)
        karens_this_day = min(absent_h, KARENS_HOURS - consumed)
        consumed += karens_this_day

    return consumed


def _get_rotation_position(session, user_id: int, on_date: date | None = None) -> int:
    """
    Returns the rotation position (person_id) a user held on a given date.

    Resolves via PersonHistory (get_user_person_id) so a mid-period rotation swap
    or succession does not retroactively change the position used for a date
    before the change. Positions without any PersonHistory rows fall back to the
    current rotation_person_id snapshot, exactly as before this resolution existed.
    """
    if session:
        from app.core.schedule.person_history import get_user_person_id
        from app.database.database import User

        user = session.query(User).filter(User.id == user_id).first()
        if user:
            position = get_user_person_id(session, user_id, on_date=on_date)
            if position is not None:
                return position
            return user.rotation_person_id
    return user_id


def get_shift_times_for_date(
    session, user_id: int, absence_date: date
) -> tuple[float, datetime.datetime | None, datetime.datetime | None]:
    """
    Hämtar (hours, start_dt, end_dt) för skiftet en person skulle ha jobbat.
    Returnerar (8.5, None, None) som fallback.

    Resolves the shift via the rotation position the user held on absence_date
    (not their current position), so a rotation swap after absence_date does not
    retroactively change how it is priced.
    """
    from app.core.schedule import calculate_shift_hours, determine_shift_for_date

    rotation_position = _get_rotation_position(session, user_id, on_date=absence_date)
    result = determine_shift_for_date(absence_date, start_week=rotation_position)
    if result and result[0]:
        shift, _ = result
        if shift and shift.code not in ["OFF", "SEM"]:
            hours, start_dt, end_dt = calculate_shift_hours(absence_date, shift.code)
            if hours > 0:
                return hours, start_dt, end_dt
    return 8.5, None, None


def get_absent_hours_for_absence(
    absence,
    shift_start_dt: datetime.datetime | None,
    shift_end_dt: datetime.datetime | None,
    shift_hours: float,
) -> float:
    """
    Calculates total absent hours for an absence record, handling left_at, arrived_at, or both.
    Falls back to shift_hours for full-day absence or when shift times are unavailable.
    """
    left_at = getattr(absence, "left_at", None)
    arrived_at = getattr(absence, "arrived_at", None)

    if not left_at and not arrived_at:
        return shift_hours

    total = 0.0

    # Hours missed at end of shift (left early)
    if left_at and shift_end_dt is not None:
        try:
            left_time = datetime.datetime.strptime(left_at, "%H:%M").time()
            left_dt = datetime.datetime.combine(shift_end_dt.date(), left_time)
            if left_dt < shift_end_dt:
                total += (shift_end_dt - left_dt).total_seconds() / 3600.0
        except ValueError:
            total += shift_hours

    # Hours missed at start of shift (arrived late)
    if arrived_at and shift_start_dt is not None:
        try:
            arrived_time = datetime.datetime.strptime(arrived_at, "%H:%M").time()
            arrived_dt = datetime.datetime.combine(shift_start_dt.date(), arrived_time)
            if arrived_dt > shift_start_dt:
                total += (arrived_dt - shift_start_dt).total_seconds() / 3600.0
        except ValueError:
            total += shift_hours

    return max(0.0, min(total, shift_hours))


def get_absence_deductions_for_month(
    session: Session,
    user_id: int,
    year: int,
    month: int,
    monthly_wage: int,
    ob_rules=None,
    ob_rate_overrides=None,
    sick_ob_compensation: bool = False,  # behålls för bakåtkompatibilitet, ignoreras när ob_rules finns
) -> dict:
    """
    Beräknar totala löneavdrag för frånvaro under en månad.

    Args:
        session: SQLAlchemy session
        user_id: Användar-ID
        year: År
        month: Månad
        monthly_wage: Månadslön i SEK
        ob_rules: OB-regler (lista av ObRule). När dessa finns slås OB-kompensation
                  upp per frånvarodatum via RateHistory för att respektera giltighetsdatum.
        ob_rate_overrides: Fallback OB-satser (används om ingen historik finns för datumet)
        sick_ob_compensation: Ignoreras när ob_rules anges (datumspecifik lookup används då)

    Returns:
        Dict med:
            - total_deduction: Totalt avdrag i SEK
            - total_hours: Totalt antal frånvarotimmar
            - sick_days: Antal sjukdagar
            - sick_hours: Antal sjuktimmar
            - sick_ob_pay: OB-ersättning vid sjukfrånvaro (SEK)
            - vab_days: Antal VAB-dagar
            - vab_hours: Antal VAB-timmar
            - leave_days: Antal lediga dagar
            - leave_hours: Antal lediga timmar
            - details: Lista med detaljer per dag
    """
    from datetime import timedelta

    from app.database.database import Absence, AbsenceType

    # Fetch all absences for the month
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
    sick_ob_pay = 0.0
    sick_ob_pay_by_code: dict[str, float] = {}
    sick_ob_hours_by_code: dict[str, float] = {}
    sick_total_ob = 0.0
    vab_days = 0
    vab_hours = 0.0
    leave_days = 0
    leave_hours = 0.0
    off_days = 0
    off_hours = 0.0
    parental_days = 0
    parental_hours = 0.0
    details = []

    # Load user once for date-specific rate lookups
    _sick_ob_user = None
    if ob_rules and session:
        from app.database.database import User as _User

        _sick_ob_user = session.query(_User).filter(_User.id == user_id).first()

    # Track waiting-day budget and sick period continuity
    last_sick_date: date | None = None
    karens_consumed_in_period = 0.0

    for absence in absences:
        shift_hours, shift_start_dt, shift_end_dt = get_shift_times_for_date(session, user_id, absence.date)
        absent_hours = get_absent_hours_for_absence(absence, shift_start_dt, shift_end_dt, shift_hours)
        total_hours += absent_hours

        if absence.absence_type == AbsenceType.SICK:
            sick_days += 1
            sick_hours += absent_hours

            if last_sick_date is None:
                # First sick day in this month: seed the budget from any karens already
                # consumed earlier in an ongoing sick period (e.g. one that started in the
                # previous month). Returns 0.0 when this is a fresh period.
                karens_consumed_in_period = get_karens_consumed_before_date(session, user_id, absence.date)
            elif (absence.date - last_sick_date).days > 5:
                # Gap > 5 days within the month: a new sick period, budget resets.
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

            # OB på frånvarodagen: beräkna alltid för att kunna visa totalt tapp
            sjuklon_hours = absent_hours - karens_today
            if ob_rules and shift_start_dt is not None and shift_end_dt is not None and shift_hours > 0:
                from app.core.schedule.ob import calculate_ob_pay

                _ob_overrides = ob_rate_overrides
                _ob_compensation = False

                if _sick_ob_user is not None:
                    from app.core.rates import get_user_rates

                    date_rates = get_user_rates(_sick_ob_user, session=session, effective_date=absence.date)
                    _ob_overrides = date_rates.get("ob") or ob_rate_overrides
                    _ob_compensation = bool(date_rates.get("sick", {}).get("ob_compensation"))

                from app.core.schedule.ob import calculate_ob_hours as _calc_ob_hours

                full_ob_by_code = calculate_ob_pay(
                    shift_start_dt, shift_end_dt, ob_rules, monthly_wage, rate_overrides=_ob_overrides
                )
                full_ob_hours_by_code = _calc_ob_hours(shift_start_dt, shift_end_dt, ob_rules)
                full_ob_total = sum(full_ob_by_code.values())
                sick_total_ob += full_ob_total * (absent_hours / shift_hours)

                if _ob_compensation and sjuklon_hours > 0:
                    hours_ratio = sjuklon_hours / shift_hours
                    ratio = hours_ratio * 0.8
                    sick_ob_pay += full_ob_total * ratio
                    for code, pay in full_ob_by_code.items():
                        if pay > 0:
                            sick_ob_pay_by_code[code] = sick_ob_pay_by_code.get(code, 0.0) + pay * ratio
                            ob_h = full_ob_hours_by_code.get(code, 0.0) or 0.0
                            sick_ob_hours_by_code[code] = sick_ob_hours_by_code.get(code, 0.0) + ob_h * hours_ratio

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
            elif absence.absence_type == AbsenceType.PARENTAL:
                parental_days += 1
                parental_hours += absent_hours

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
        "sick_ob_pay": sick_ob_pay,
        "sick_ob_pay_by_code": sick_ob_pay_by_code,
        "sick_ob_hours_by_code": sick_ob_hours_by_code,
        "sick_total_ob": sick_total_ob,
        "sick_ob_lost": sick_total_ob - sick_ob_pay,
        "vab_days": vab_days,
        "vab_hours": vab_hours,
        "leave_days": leave_days,
        "leave_hours": leave_hours,
        "off_days": off_days,
        "off_hours": off_hours,
        "parental_days": parental_days,
        "parental_hours": parental_hours,
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


def update_wage_value(session: Session, wage_id: int, user_id: int, new_wage: int):
    """
    Uppdaterar beloppet på en befintlig lönepost utan att röra datumen.

    Endast lönebeloppet ändras; effective_from/effective_to lämnas orörda så
    tidslinjens integritet inte kan gå sönder av redigeringen.

    Args:
        session: SQLAlchemy session
        wage_id: ID på löneposten som ska uppdateras
        user_id: Ägaren posten måste tillhöra (behörighetskontroll)
        new_wage: Nytt lönebelopp i SEK

    Returns:
        Den uppdaterade WageHistory-posten

    Raises:
        LookupError: om posten inte finns
        PermissionError: om posten tillhör en annan användare
    """
    from app.database.database import User, WageHistory

    record = session.query(WageHistory).filter(WageHistory.id == wage_id).first()
    if not record:
        raise LookupError("Wage record not found")
    if record.user_id != user_id:
        raise PermissionError("Wage record does not belong to this user")

    record.wage = new_wage

    # Keep User.wage in sync if this is the active wage record
    if record.effective_to is None and record.effective_from <= get_today():
        user = session.query(User).filter(User.id == user_id).first()
        if user:
            user.wage = new_wage

    session.commit()

    return record


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
