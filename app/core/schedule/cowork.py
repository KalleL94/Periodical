"""Samarbetsstatistik - vem jobbar med vem."""

import datetime

from app.core.constants import PERSON_IDS
from app.core.storage import load_persons

from .period import generate_year_data

_persons = None


def _get_persons():
    global _persons
    if _persons is None:
        _persons = load_persons()
    return _persons


def _get_person_name_from_db(person_id: int) -> str:
    """Get current person name from database (respects person_id field)."""
    from app.database.database import SessionLocal, User

    db = SessionLocal()
    try:
        # First check if someone has this person_id explicitly set
        holder = db.query(User).filter(User.person_id == person_id).first()
        if holder:
            return holder.name
        # Fallback: legacy user where user_id == person_id
        user = db.query(User).filter(User.id == person_id).first()
        if user:
            return user.name
        # Final fallback: JSON file
        return _get_persons()[person_id - 1].name
    finally:
        db.close()


def build_cowork_stats(year: int, target_person_id: int) -> list[dict]:
    """
    Räknar hur många pass target_person_id jobbar tillsammans
    med varje annan person, samt beräknar överlämnings- och månadsstatistik.

    En dag räknas som samarbete bara om båda:
      - jobbar (inte OFF)
      - har SAMMA passtyp (N1, N2 eller N3)

    Överlämningar räknas för:
      - Samma dag: N1↔N2 eller N2↔N3
      - Korsdag: N3 (dag D) → N1 (dag D+1)

    Args:
        year: År att analysera
        target_person_id: Person att analysera

    Returns:
        Lista med statistik per medarbetare, sorterad på person-ID
    """
    today = datetime.date.today()
    days_in_year = generate_year_data(year, person_id=None)

    total_target_work_days = 0

    # Initiera statistik för alla andra personer
    stats: dict[int, dict] = {}
    for pid in PERSON_IDS:
        if pid == target_person_id:
            continue

        stats[pid] = {
            "other_id": pid,
            "other_name": _get_person_name_from_db(pid),
            "total": 0,
            "by_shift": {"N1": 0, "N2": 0, "N3": 0},
            "by_month": {m: 0 for m in range(1, 13)},
            "by_weekday": {d: 0 for d in range(7)},
            "handovers": 0,
            "pct": 0.0,
            "next_shared_date": None,
            "next_handover_date": None,
        }

    # Överlämningspar samma dag: skiftet till vänster lämnar till skiftet till höger
    _HANDOVER_PAIRS = {("N1", "N2"), ("N2", "N3")}

    prev_day_shifts: dict[int, str] = {}  # person_id -> skiftkod föregående dag

    for day in days_in_year:
        persons_today = day.get("persons", [])

        # Bygg dagens skiftkarta
        current_day_shifts: dict[int, str] = {
            p["person_id"]: (p["shift"].code if p.get("shift") else "OFF") for p in persons_today
        }

        target_prev = prev_day_shifts.get(target_person_id, "OFF")

        # Hitta target-personens skift idag
        target = _find_person_in_day(persons_today, target_person_id)
        target_code = "OFF"
        if target and target.get("shift"):
            target_code = target["shift"].code

        date_val = day["date"]
        month = date_val.month
        weekday = date_val.weekday()

        # Korsdag N3→N1: kontrollera mot gårdagens skift
        for pid in stats:
            other_prev = prev_day_shifts.get(pid, "OFF")
            other_curr = current_day_shifts.get(pid, "OFF")
            if (target_prev == "N3" and other_curr == "N1") or (other_prev == "N3" and target_code == "N1"):
                stats[pid]["handovers"] += 1
                if date_val >= today and stats[pid]["next_handover_date"] is None:
                    stats[pid]["next_handover_date"] = date_val

        # Uppdatera prev innan eventuellt skip
        prev_day_shifts = current_day_shifts

        if target_code not in ("N1", "N2", "N3"):
            continue

        total_target_work_days += 1

        # Jämför med alla andra
        for p in persons_today:
            pid = p["person_id"]
            if pid == target_person_id or pid not in stats:
                continue

            other_shift = p.get("shift")
            if not other_shift:
                continue

            other_code = other_shift.code

            # Samarbete: samma skifttyp
            if other_code == target_code:
                stats[pid]["total"] += 1
                stats[pid]["by_shift"][target_code] += 1
                stats[pid]["by_month"][month] += 1
                stats[pid]["by_weekday"][weekday] += 1
                if date_val >= today and stats[pid]["next_shared_date"] is None:
                    stats[pid]["next_shared_date"] = date_val

            # Överlämning samma dag: N1↔N2 eller N2↔N3
            pair = (target_code, other_code)
            pair_rev = (other_code, target_code)
            if pair in _HANDOVER_PAIRS or pair_rev in _HANDOVER_PAIRS:
                stats[pid]["handovers"] += 1
                if date_val >= today and stats[pid]["next_handover_date"] is None:
                    stats[pid]["next_handover_date"] = date_val

    # Beräkna procentandelar
    rows = list(stats.values())
    for r in rows:
        r["pct"] = round(r["total"] / total_target_work_days * 100, 1) if total_target_work_days > 0 else 0.0

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
                "rotation_length": target.get("rotation_length"),
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


def build_handover_details(
    year: int,
    target_person_id: int,
    other_person_id: int,
) -> list[dict]:
    """
    Returnerar alla överlämningar mellan två personer under ett år.

    En överlämning räknas när:
      - Samma dag: ett pass avslutas och nästa startar (N1→N2 eller N2→N3)
      - Korsdag: N3 (dag D) → N1 (dag D+1), datum sätts till dag D+1

    Varje post innehåller:
      - date, weekday_name: när överlämningen sker
      - from_shift: skiftet som lämnar (t.ex. "N1")
      - to_shift: skiftet som tar emot (t.ex. "N2")
      - i_lamnar: True om target är den som lämnar, False om other lämnar

    Args:
        year: År att analysera
        target_person_id: "Jag"-personen
        other_person_id: Den andra personen

    Returns:
        Lista med överlämningar, sorterad på datum
    """
    days_in_year = generate_year_data(year, person_id=None)
    details: list[dict] = []

    _HANDOVER_PAIRS = {("N1", "N2"), ("N2", "N3")}

    prev_target_code = "OFF"
    prev_other_code = "OFF"

    for day in days_in_year:
        persons_today = day.get("persons", [])

        target = _find_person_in_day(persons_today, target_person_id)
        other = _find_person_in_day(persons_today, other_person_id)

        target_code = target["shift"].code if target and target.get("shift") else "OFF"
        other_code = other["shift"].code if other and other.get("shift") else "OFF"

        date = day["date"]
        weekday_name = day["weekday_name"]

        # Korsdag N3→N1: använder dagens datum (när N1 börjar)
        if prev_target_code == "N3" and other_code == "N1":
            details.append(
                {
                    "date": date,
                    "weekday_name": weekday_name,
                    "from_shift": "N3",
                    "to_shift": "N1",
                    "i_lamnar": True,  # target (N3 igår) lämnade till other (N1 idag)
                    "cross_day": True,
                }
            )
        elif prev_other_code == "N3" and target_code == "N1":
            details.append(
                {
                    "date": date,
                    "weekday_name": weekday_name,
                    "from_shift": "N3",
                    "to_shift": "N1",
                    "i_lamnar": False,  # other (N3 igår) lämnade till target (N1 idag)
                    "cross_day": True,
                }
            )

        # Samma dag: N1→N2 eller N2→N3
        pair = (target_code, other_code)
        pair_rev = (other_code, target_code)
        if pair in _HANDOVER_PAIRS:
            details.append(
                {
                    "date": date,
                    "weekday_name": weekday_name,
                    "from_shift": target_code,
                    "to_shift": other_code,
                    "i_lamnar": True,  # target lämnar till other
                    "cross_day": False,
                }
            )
        elif pair_rev in _HANDOVER_PAIRS:
            details.append(
                {
                    "date": date,
                    "weekday_name": weekday_name,
                    "from_shift": other_code,
                    "to_shift": target_code,
                    "i_lamnar": False,  # other lämnar till target
                    "cross_day": False,
                }
            )

        prev_target_code = target_code
        prev_other_code = other_code

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
