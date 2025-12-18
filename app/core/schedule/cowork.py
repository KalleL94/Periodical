"""Samarbetsstatistik - vem jobbar med vem."""

from app.core.constants import PERSON_IDS
from app.core.storage import load_persons

from .period import generate_year_data

_persons = None


def _get_persons():
    global _persons
    if _persons is None:
        _persons = load_persons()
    return _persons


def build_cowork_stats(year: int, target_person_id: int) -> list[dict]:
    """
    Räknar hur många pass target_person_id jobbar tillsammans
    med varje annan person.

    En dag räknas bara om båda:
      - jobbar (inte OFF)
      - har SAMMA passtyp (N1, N2 eller N3)

    Args:
        year: År att analysera
        target_person_id: Person att analysera

    Returns:
        Lista med statistik per medarbetare, sorterad på person-ID
    """
    days_in_year = generate_year_data(year, person_id=None)
    persons = _get_persons()

    # Initiera statistik för alla andra personer
    stats: dict[int, dict] = {}
    for pid in PERSON_IDS:
        if pid == target_person_id:
            continue

        stats[pid] = {
            "other_id": pid,
            "other_name": persons[pid - 1].name,
            "total": 0,
            "by_shift": {"N1": 0, "N2": 0, "N3": 0},
        }

    # Gå igenom alla dagar
    for day in days_in_year:
        persons_today = day.get("persons", [])
        if not persons_today:
            continue

        # Hitta target-personens skift
        target = _find_person_in_day(persons_today, target_person_id)
        if not target:
            continue

        target_shift = target.get("shift")
        if not target_shift or target_shift.code == "OFF":
            continue

        target_code = target_shift.code

        # Jämför med alla andra
        for p in persons_today:
            pid = p["person_id"]
            if pid == target_person_id:
                continue

            other_shift = p.get("shift")
            if not other_shift or other_shift.code == "OFF":
                continue

            # Bara samma skifttyp räknas
            if other_shift.code != target_code:
                continue

            stats[pid]["total"] += 1
            if target_code in stats[pid]["by_shift"]:
                stats[pid]["by_shift"][target_code] += 1

    # Sortera och returnera
    rows = list(stats.values())
    rows.sort(key=lambda r: r["other_id"])
    return rows


def build_cowork_details(
    year: int,
    target_person_id: int,
    other_person_id: int,
) -> list[dict]:
    """
    Returnerar alla dagar då två personer jobbar samma skift.

    Args:
        year: År att analysera
        target_person_id: Första personen
        other_person_id: Andra personen

    Returns:
        Lista med dagdetaljer, sorterad på datum
    """
    days_in_year = generate_year_data(year, person_id=None)
    details: list[dict] = []

    for day in days_in_year:
        persons_today = day.get("persons", [])
        if not persons_today:
            continue

        target = _find_person_in_day(persons_today, target_person_id)
        other = _find_person_in_day(persons_today, other_person_id)

        if not target or not other:
            continue

        target_shift = target.get("shift")
        other_shift = other.get("shift")

        # Båda måste jobba
        if not target_shift or target_shift.code == "OFF":
            continue
        if not other_shift or other_shift.code == "OFF":
            continue

        # Samma skifttyp
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


def _find_person_in_day(persons_today: list[dict], person_id: int) -> dict | None:
    """Hittar en person i dagens personlista."""
    return next(
        (p for p in persons_today if p["person_id"] == person_id),
        None,
    )
