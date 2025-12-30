"""Grundläggande schemalogik och skiftbestämning."""

import datetime
from functools import cache
from typing import TYPE_CHECKING

from app.core.config import DATE_FORMAT_ISO
from app.core.constants import VACATION_CODE, WEEKDAY_NAMES
from app.core.storage import load_rotation, load_settings, load_shift_types
from app.database import database as db_module
from app.database.database import RotationEra

if TYPE_CHECKING:
    from app.core.models import Rotation, Settings, ShiftType

# === Lazy-loaded data ===
_shift_types: list["ShiftType"] | None = None
_rotation: "Rotation | None" = None
_settings: "Settings | None" = None
_rotation_start_date: datetime.date | None = None

# Exponera veckodagsnamn
weekday_names = list(WEEKDAY_NAMES)


def _ensure_loaded() -> None:
    """Lazy-load av konfigurationsdata."""
    global _shift_types, _rotation, _settings, _rotation_start_date
    if _shift_types is None:
        _shift_types = load_shift_types()
        _rotation = load_rotation()
        _settings = load_settings()
        _rotation_start_date = datetime.datetime.strptime(_settings.rotation_start_date, DATE_FORMAT_ISO).date()


def get_shift_types() -> list["ShiftType"]:
    _ensure_loaded()
    return _shift_types  # type: ignore


def get_rotation() -> "Rotation":
    _ensure_loaded()
    return _rotation  # type: ignore


def get_settings() -> "Settings":
    _ensure_loaded()
    return _settings  # type: ignore


def get_rotation_start_date() -> datetime.date:
    _ensure_loaded()
    return _rotation_start_date  # type: ignore


def get_vacation_shift() -> "ShiftType | None":
    """Returnerar semester-skifttypen."""
    return next((s for s in get_shift_types() if s.code == VACATION_CODE), None)


@cache
def get_rotation_era_for_date(date: datetime.date) -> RotationEra | None:
    """
    Hämtar den rotation era som är aktiv för ett givet datum.

    Args:
        date: Datum att hitta era för

    Returns:
        RotationEra om en matchande era finns, annars None
    """
    # Use db_module.SessionLocal() to get the current version (supports monkeypatch in tests)
    db = db_module.SessionLocal()
    try:
        # Hitta era där date >= start_date OCH (end_date är NULL ELLER date < end_date)
        era = (
            db.query(RotationEra)
            .filter(RotationEra.start_date <= date)
            .filter((RotationEra.end_date.is_(None)) | (RotationEra.end_date > date))
            .order_by(RotationEra.start_date.desc())
            .first()
        )
        return era
    finally:
        db.close()


def get_rotation_length_for_date(date: datetime.date) -> int | None:
    """
    Hämtar rotationslängden som är aktiv för ett givet datum.

    Args:
        date: Datum att hitta rotationslängd för

    Returns:
        Antal veckor i rotationen för det datumet, eller None om ingen era finns
    """
    era = get_rotation_era_for_date(date)
    return era.rotation_length if era else None


@cache
def determine_shift_for_date(
    date: datetime.date,
    start_week: int = 1,
) -> tuple["ShiftType | None", int | None]:
    """
    Bestämmer skift för ett datum baserat på rotation era.

    Args:
        date: Datum att kontrollera
        start_week: Personens startvecka i rotationen

    Returns:
        (shift, rotation_week) eller (None, None) om ingen era finns för datumet
    """
    # Hämta rätt rotation era för datumet
    era = get_rotation_era_for_date(date)
    if era is None:
        return None, None

    shift_types = get_shift_types()
    rotation_start = era.start_date

    # Beräkna första måndagen efter rotationsstart
    days_to_monday = (7 - rotation_start.weekday()) % 7
    if days_to_monday == 0 and rotation_start.weekday() != 0:
        days_to_monday = 7 - rotation_start.weekday()

    first_monday = rotation_start + datetime.timedelta(days=days_to_monday)

    # Beräkna veckor sedan start
    if date < first_monday:
        weeks_passed = 0
    else:
        weeks_passed = 1 + ((date - first_monday).days // 7)

    # Använd erans rotation_length för modulo-beräkningen
    rotation_week = ((weeks_passed + (start_week - 1)) % era.rotation_length) + 1
    weekday_index = date.weekday()

    # Använd erans weeks_pattern
    shift_code = era.weeks_pattern[str(rotation_week)][weekday_index]

    shift = next((s for s in shift_types if s.code == shift_code), None)
    return shift, rotation_week


@cache
def _calculate_shift_hours_cached(
    date: datetime.date,
    shift_code: str,
) -> tuple[float, datetime.datetime | None, datetime.datetime | None]:
    """Intern cachad version som tar shift_code som sträng."""
    shift = next((s for s in get_shift_types() if s.code == shift_code), None)

    if shift is None or shift.code == "OFF":
        return 0.0, None, None

    start_time = datetime.datetime.strptime(shift.start_time, "%H:%M").time()
    end_time = datetime.datetime.strptime(shift.end_time, "%H:%M").time()

    start_dt = datetime.datetime.combine(date, start_time)
    end_dt = datetime.datetime.combine(date, end_time)

    # Hantera pass över midnatt
    if end_time <= start_time:
        end_dt += datetime.timedelta(days=1)

    hours = (end_dt - start_dt).total_seconds() / 3600.0
    return hours, start_dt, end_dt


def calculate_shift_hours(
    date: datetime.date,
    shift,
) -> tuple[float, datetime.datetime | None, datetime.datetime | None]:
    """
    Beräknar arbetstimmar och start/slut för ett skift.

    Args:
        date: Datum för skiftet
        shift: ShiftType-objekt eller skiftkod (sträng)

    Returns:
        (hours, start_datetime, end_datetime)
    """
    # Hantera både ShiftType-objekt och strängar
    if shift is None:
        return 0.0, None, None

    if isinstance(shift, str):
        shift_code = shift
    else:
        # Anta att det är ett ShiftType-objekt
        shift_code = shift.code

    if shift_code == "OFF":
        return 0.0, None, None

    return _calculate_shift_hours_cached(date, shift_code)


def clear_schedule_cache() -> None:
    """Rensar alla cachade schemaberäkningar."""
    get_rotation_era_for_date.cache_clear()
    determine_shift_for_date.cache_clear()
    _calculate_shift_hours_cached.cache_clear()

    # Rensa även i andra moduler
    try:
        from . import ob

        ob.get_special_rules_for_year.cache_clear()
    except (ImportError, AttributeError):
        pass
