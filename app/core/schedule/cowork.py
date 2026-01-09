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


def get_coworkers_for_day(
    target_person_id: int,
    target_shift_code: str,
    persons_on_day: list[dict],
    target_start=None,
    target_end=None,
) -> list[str]:
    """
    Returns list of coworker names who work the same shift on this day.

    Matching logic:
    - Both must work (not OFF, SICK, etc.)
    - Same shift type (N1, N2, or N3)
    - For OT shifts: matches if original_shift matches, OR if times overlap significantly

    Args:
        target_person_id: The person whose coworkers we're finding
        target_shift_code: The shift code (N1, N2, N3, etc.)
        persons_on_day: List of person data for this day
        target_start: Start datetime of target person's shift (for OT overlap checking)
        target_end: End datetime of target person's shift (for OT overlap checking)

    Returns:
        List of coworker names (sorted by person_id), excluding target person
    """
    from app.core.constants import WORK_SHIFT_CODES

    # Only show coworkers for actual work shifts or OT (with time matching)
    if target_shift_code not in WORK_SHIFT_CODES and target_shift_code != "OT":
        return []

    coworkers = []

    # If target is OT without a known work shift, use time-based matching for everyone
    use_time_matching_for_target = target_shift_code == "OT"

    for person_data in persons_on_day:
        person_id = person_data.get("person_id")

        # Skip self
        if person_id == target_person_id:
            continue

        actual_shift = person_data.get("shift")
        if not actual_shift:
            continue

        # Skip if person is not actually working
        # OFF, SICK, VAB, LEAVE, SEM, OC should not count as working together
        non_work_codes = ("OFF", "SICK", "VAB", "LEAVE", "SEM", "OC")
        if actual_shift.code in non_work_codes:
            continue

        # Check if this person matches
        is_match = False

        if use_time_matching_for_target:
            # Target has OT, match anyone who works and has time overlap
            if target_start and target_end:
                other_start = person_data.get("start")
                other_end = person_data.get("end")
                if other_start and other_end:
                    # Check if shifts overlap significantly (at least 4 hours)
                    overlap = _calculate_overlap_hours(target_start, target_end, other_start, other_end)
                    if overlap >= 4.0:
                        is_match = True
        elif actual_shift.code == "OT":
            # Person has OT, target has regular shift: check if original_shift matches, or if times overlap
            original_shift = person_data.get("original_shift")
            if original_shift and original_shift.code == target_shift_code:
                # OT replacing a shift that matches target
                is_match = True
            elif target_start and target_end:
                # Check time overlap
                other_start = person_data.get("start")
                other_end = person_data.get("end")
                if other_start and other_end:
                    # Check if shifts overlap significantly (at least 4 hours)
                    overlap = _calculate_overlap_hours(target_start, target_end, other_start, other_end)
                    if overlap >= 4.0:
                        is_match = True
        else:
            # Regular work shift (N1, N2, N3)
            # Use original_shift if available (for when target has OT)
            original_shift = person_data.get("original_shift")
            shift_for_matching = original_shift if original_shift else actual_shift

            if shift_for_matching.code == target_shift_code:
                is_match = True

        if is_match:
            coworkers.append(
                {
                    "id": person_id,
                    "name": person_data.get("person_name", ""),
                }
            )

    # Sort by person_id and return just names
    coworkers.sort(key=lambda x: x["id"])
    return [c["name"] for c in coworkers]


def _calculate_overlap_hours(start1, end1, start2, end2) -> float:
    """Calculate overlap hours between two time periods."""
    # Find the overlap period
    overlap_start = max(start1, start2)
    overlap_end = min(end1, end2)

    if overlap_start >= overlap_end:
        return 0.0

    overlap_seconds = (overlap_end - overlap_start).total_seconds()
    return overlap_seconds / 3600.0


def _find_person_in_day(persons_today: list[dict], person_id: int) -> dict | None:
    """Hittar en person i dagens personlista."""
    return next(
        (p for p in persons_today if p["person_id"] == person_id),
        None,
    )
