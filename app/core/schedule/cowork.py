"""Samarbetsstatistik - vem jobbar med vem."""

import datetime

from app.core.constants import PERSON_IDS, placeholder_person_name

from .period import generate_year_data

# Cowork statistics are about PEOPLE, not rotation positions. A position changes
# hands and a person moves between positions, so both sides of every comparison
# are resolved per date: who sat at this position today, and where did that
# person sit today. Rows are keyed by a holder key, ("u", user_id) for a tracked
# user and ("p", position) for a position with no PersonHistory at all, which
# keeps legacy setups behaving exactly as they did before employment was tracked.


class _Holders:
    """Who held which rotation position on each date of one year.

    Built once per call from PersonHistory. A None session, or a position with
    no history, falls back to the legacy view where the position itself stands
    in for a person.
    """

    def __init__(self, session, year: int):
        from app.core.schedule.person_history import (
            get_position_holder_segments,
            has_position_history,
        )

        self.year_start = datetime.date(year, 1, 1)
        self.year_end = datetime.date(year, 12, 31)

        self._by_position: dict[int, list[tuple]] = {}
        self._by_key: dict[tuple, list[tuple]] = {}
        self.name: dict[tuple, str] = {}
        self.active: dict[tuple, bool] = {}
        self.row_position: dict[tuple, int] = {}
        self.key_at_row_position: dict[int, tuple] = {}

        dated: list[tuple] = []  # (from_date, key, position), sorted later
        for pid in PERSON_IDS:
            if session is None or not has_position_history(session, pid):
                key = ("p", pid)
                self._by_position[pid] = [(self.year_start, self.year_end, key)]
                self._by_key.setdefault(key, []).append((self.year_start, self.year_end, pid))
                self.name[key] = placeholder_person_name(pid)
                self.active[key] = True
                dated.append((self.year_start, key, pid))
                continue

            spans: list[tuple] = []
            for seg in get_position_holder_segments(session, pid, self.year_start, self.year_end):
                key = ("u", seg["user_id"])
                spans.append((seg["from_date"], seg["to_date"], key))
                self._by_key.setdefault(key, []).append((seg["from_date"], seg["to_date"], pid))
                self.name[key] = seg["name"]
                # An open record anywhere means the person is still employed.
                # PersonHistory.is_active is not kept in step, so it is not used.
                self.active[key] = self.active.get(key, False) or seg["effective_to"] is None
                dated.append((seg["from_date"], key, pid))
            self._by_position[pid] = spans

        # Someone who moved during the year is filed under, and linked by, the
        # last position they held: the one the rest of the app resolves them to.
        for _from_date, key, pid in sorted(dated, key=lambda t: t[0]):
            self.row_position[key] = pid
            self.key_at_row_position[pid] = key

    def holder_at(self, position: int, day_date) -> tuple | None:
        """The holder key at a position on a date, or None when it is vacant.

        Scanned newest first, matching get_person_for_date, so overlapping
        records resolve to the one that started last.
        """
        for lo, hi, key in reversed(self._by_position.get(position, ())):
            if lo <= day_date <= hi:
                return key
        return None

    def position_at(self, key, day_date) -> int | None:
        """The position a holder sat at on a date, or None when they held none."""
        for lo, hi, pid in reversed(self._by_key.get(key, ())):
            if lo <= day_date <= hi:
                return pid
        return None


def _viewer_segments(session, employment_user_id, year: int):
    """Return the positions the viewed user held during the year, in order.

    Returns None when no scoping should apply: the caller supplied no
    session/user, or the user has no PersonHistory at all (legacy users keep the
    whole-year-at-the-passed-position behavior). Otherwise returns a list of
    segment dicts with person_id, from_date and to_date clamped to the year. The
    list may be empty when the user held no position in the year, which masks
    every day.

    Scoping follows the USER, not the position: a user who swapped rotation
    position works a different position in each part of the year, and the caller
    only knows the one resolved for today. Masking against that single position
    emptied the whole page for anyone who had moved.
    """
    if session is None or employment_user_id is None:
        return None

    from app.core.schedule.person_history import get_user_history, get_user_position_segments

    if not get_user_history(session, employment_user_id):
        return None

    year_start = datetime.date(year, 1, 1)
    year_end = datetime.date(year, 12, 31)
    return get_user_position_segments(session, employment_user_id, year_start, year_end)


def _position_on(day_date, segments, default_position: int) -> int | None:
    """The position the viewed user held on day_date.

    default_position when scoping is off (segments is None), otherwise the
    matching segment's position, or None when the user held no position that day.
    """
    if segments is None:
        return default_position
    for seg in segments:
        if seg["from_date"] <= day_date <= seg["to_date"]:
            return seg["person_id"]
    return None


def _viewer_key(holders: _Holders, employment_user_id, target_person_id: int) -> tuple:
    """The row key belonging to the viewed person, so they are not their own coworker."""
    if employment_user_id is not None and ("u", employment_user_id) in holders.name:
        return ("u", employment_user_id)
    return holders.key_at_row_position.get(target_person_id, ("p", target_person_id))


def _other_key(holders: _Holders, other_person_id: int, other_user_id: int | None):
    """The row key the detail views were opened for.

    other_user_id names the person outright and is what the stat table links
    with. other_person_id is the position they are filed under, kept so older
    links (and any caller that only knows a position) still resolve to whoever
    held it last that year. None means no holder is known, and the caller reads
    the position as fixed for the whole year.
    """
    if other_user_id is not None and ("u", other_user_id) in holders.name:
        return ("u", other_user_id)
    return holders.key_at_row_position.get(other_person_id)


def _new_row(holders: _Holders, key: tuple) -> dict:
    """An empty statistics row for one coworker."""
    return {
        "other_id": holders.row_position.get(key, key[1] if key[0] == "p" else 0),
        "other_user_id": key[1] if key[0] == "u" else None,
        "other_name": holders.name.get(key) or placeholder_person_name(holders.row_position.get(key, 0)),
        "active": holders.active.get(key, True),
        "total": 0,
        "by_shift": {"N1": 0, "N2": 0, "N3": 0},
        "by_month": {m: 0 for m in range(1, 13)},
        "by_weekday": {d: 0 for d in range(7)},
        "handovers": 0,
        "pct": 0.0,
        "next_shared_date": None,
        "next_handover_date": None,
    }


def build_cowork_stats(
    year: int,
    target_person_id: int,
    session=None,
    employment_user_id: int | None = None,
    days_in_year: list[dict] | None = None,
) -> list[dict]:
    """
    Räknar hur många pass den visade personen jobbar tillsammans med varje
    annan person, samt beräknar överlämnings- och månadsstatistik.

    En dag räknas som samarbete bara om båda:
      - jobbar (inte OFF)
      - har SAMMA passtyp (N1, N2 eller N3)

    Överlämningar räknas för:
      - Samma dag: N1↔N2 eller N2↔N3
      - Korsdag: N3 (dag D) → N1 (dag D+1)

    Args:
        year: År att analysera
        target_person_id: Position (rotation person_id) att analysera. Only the
            fallback for users without PersonHistory: with history the viewed
            person's position is resolved per date.
        session: Optional DB session. With it, every position is read at the
            holder who sat there on each date, so one row is one person even
            when they moved position during the year, and the viewed person's
            own positions are never counted as a coworker.
        employment_user_id: Optional user id of the viewed person. When given
            together with session, each day is read at the position that user's
            PersonHistory puts them on, and days they held no position are
            treated as OFF, so a successor's interactions are not attributed to
            a departed holder.

    Returns:
        Lista med statistik per medarbetare, sorterad på namn med inaktiva sist
    """
    today = datetime.date.today()
    # The session is what makes absences, swaps and overtime apply. Without it
    # every day is the raw rotation shift, so a sick day still counted as a
    # shared shift. Building the year costs seconds, so a caller making several
    # of these calls builds it once and passes it in.
    if days_in_year is None:
        days_in_year = generate_year_data(year, person_id=None, session=session)
    holders = _Holders(session, year)
    segments = _viewer_segments(session, employment_user_id, year)
    me = _viewer_key(holders, employment_user_id, target_person_id)

    total_target_work_days = 0

    # Rows are created as colleagues are met, see the day loop below.
    stats: dict[tuple, dict] = {}

    # Överlämningspar samma dag: skiftet till vänster lämnar till skiftet till höger
    _HANDOVER_PAIRS = {("N1", "N2"), ("N2", "N3")}

    prev_codes: dict[tuple, str] = {}  # holder key -> skiftkod föregående dag
    target_prev = "OFF"

    for day in days_in_year:
        persons_today = day.get("persons", [])
        date_val = day["date"]

        shift_at: dict[int, str] = {
            p["person_id"]: (p["shift"].code if p.get("shift") else "OFF") for p in persons_today
        }

        # Dagens skiftkod per person, oavsett vilken position de sitter på.
        current_codes: dict[tuple, str] = {}
        for pid in PERSON_IDS:
            key = holders.holder_at(pid, date_val)
            if key is not None:
                current_codes[key] = shift_at.get(pid, "OFF")

        # The position the viewed person actually sat at on this date. None means
        # they held no position that day, so nothing is attributed to them and
        # the carried-forward state stays OFF across the tenure edge.
        position_today = _position_on(date_val, segments, target_person_id)
        if position_today is None:
            prev_codes = current_codes
            target_prev = "OFF"
            continue

        target_code = shift_at.get(position_today, "OFF")

        # Anyone holding a position on a day the viewed person also holds one is
        # a colleague, and gets a row even with no shared shift. A successor who
        # only held the viewed person's own position after they left never turns
        # up here, which is right: they never worked together.
        for key in current_codes:
            if key != me and key not in stats:
                stats[key] = _new_row(holders, key)

        month = date_val.month
        weekday = date_val.weekday()

        # Korsdag N3→N1: kontrollera mot gårdagens skift
        for key, row in stats.items():
            other_prev = prev_codes.get(key, "OFF")
            other_curr = current_codes.get(key, "OFF")
            if (target_prev == "N3" and other_curr == "N1") or (other_prev == "N3" and target_code == "N1"):
                row["handovers"] += 1
                if date_val >= today and row["next_handover_date"] is None:
                    row["next_handover_date"] = date_val

        # Uppdatera prev innan eventuellt skip
        prev_codes = current_codes
        target_prev = target_code

        if target_code not in ("N1", "N2", "N3"):
            continue

        total_target_work_days += 1

        # Jämför med alla andra
        for key, other_code in current_codes.items():
            row = stats.get(key)
            if row is None or other_code not in ("N1", "N2", "N3"):
                continue

            # Samarbete: samma skifttyp
            if other_code == target_code:
                row["total"] += 1
                row["by_shift"][target_code] += 1
                row["by_month"][month] += 1
                row["by_weekday"][weekday] += 1
                if date_val >= today and row["next_shared_date"] is None:
                    row["next_shared_date"] = date_val

            # Överlämning samma dag: N1↔N2 eller N2↔N3
            pair = (target_code, other_code)
            pair_rev = (other_code, target_code)
            if pair in _HANDOVER_PAIRS or pair_rev in _HANDOVER_PAIRS:
                row["handovers"] += 1
                if date_val >= today and row["next_handover_date"] is None:
                    row["next_handover_date"] = date_val

    # Beräkna procentandelar
    rows = list(stats.values())
    for r in rows:
        r["pct"] = round(r["total"] / total_target_work_days * 100, 1) if total_target_work_days > 0 else 0.0

    # Alphabetical, with everyone who has left the rotation last.
    rows.sort(key=lambda r: (not r["active"], r["other_name"].casefold()))
    return rows


def build_cowork_details(
    year: int,
    target_person_id: int,
    other_person_id: int,
    session=None,
    employment_user_id: int | None = None,
    other_user_id: int | None = None,
    days_in_year: list[dict] | None = None,
) -> list[dict]:
    """
    Returnerar alla dagar då två personer jobbar samma skift.

    Args:
        year: År att analysera
        target_person_id: Position för den visade personen, fallback utan historik
        other_person_id: Position kollegan är listad under
        session: Optional DB session, see build_cowork_stats
        employment_user_id: Optional user id of the viewed person, see build_cowork_stats
        other_user_id: Optional user id of the coworker. Both sides follow their
            person across position changes, so a day counts whenever the two
            worked the same shift, wherever either of them sat.

    Returns:
        Lista med dagdetaljer, sorterad på datum
    """
    # The session is what makes absences, swaps and overtime apply. Without it
    # every day is the raw rotation shift, so a sick day still counted as a
    # shared shift. Building the year costs seconds, so a caller making several
    # of these calls builds it once and passes it in.
    if days_in_year is None:
        days_in_year = generate_year_data(year, person_id=None, session=session)
    holders = _Holders(session, year)
    segments = _viewer_segments(session, employment_user_id, year)
    other_key = _other_key(holders, other_person_id, other_user_id)
    details: list[dict] = []

    for day in days_in_year:
        persons_today = day.get("persons", [])
        if not persons_today:
            continue

        date_val = day["date"]
        my_position = _position_on(date_val, segments, target_person_id)
        their_position = other_person_id if other_key is None else holders.position_at(other_key, date_val)
        if my_position is None or their_position is None or my_position == their_position:
            continue

        target = _find_person_in_day(persons_today, my_position)
        other = _find_person_in_day(persons_today, their_position)

        if not target or not other:
            continue

        target_shift = target.get("shift")
        other_shift = other.get("shift")

        # Båda måste jobba ett riktigt arbetspass (N1/N2/N3)
        if not target_shift or target_shift.code not in ("N1", "N2", "N3"):
            continue
        if not other_shift or other_shift.code not in ("N1", "N2", "N3"):
            continue

        # Samma skifttyp
        if target_shift.code != other_shift.code:
            continue

        details.append(
            {
                "date": date_val,
                "weekday_name": day["weekday_name"],
                "rotation_week": target.get("rotation_week"),
                "rotation_length": target.get("rotation_length"),
                "target_id": my_position,
                "target_name": target["person_name"],
                "target_shift": target_shift,
                "other_id": their_position,
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
    session=None,
    employment_user_id: int | None = None,
    other_user_id: int | None = None,
    days_in_year: list[dict] | None = None,
) -> list[dict]:
    """
    Returnerar alla överlämningar mellan två personer under ett år.

    En överlämning räknas när:
      - Samma dag: ett pass avslutas och nästa startar (N1→N2 eller N2→N3)
      - Korsdag: N3 (dag D) → N1 (dag D+1), datum sätts till dag D+1

    Varje post innehåller:
      - date, weekday_name: när överlämningen sker
      - from_shift, to_shift: skiftet som lämnar och skiftet som tar emot
      - i_lamnar: True om den visade personen är den som lämnar

    Args:
        year: År att analysera
        target_person_id: Position för den visade personen, fallback utan historik
        other_person_id: Position kollegan är listad under
        session: Optional DB session, see build_cowork_stats
        employment_user_id: Optional user id of the viewed person, see build_cowork_stats
        other_user_id: Optional user id of the coworker, see build_cowork_details

    Returns:
        Lista med överlämningar, sorterad på datum
    """
    # The session is what makes absences, swaps and overtime apply. Without it
    # every day is the raw rotation shift, so a sick day still counted as a
    # shared shift. Building the year costs seconds, so a caller making several
    # of these calls builds it once and passes it in.
    if days_in_year is None:
        days_in_year = generate_year_data(year, person_id=None, session=session)
    holders = _Holders(session, year)
    segments = _viewer_segments(session, employment_user_id, year)
    other_key = _other_key(holders, other_person_id, other_user_id)
    details: list[dict] = []

    _HANDOVER_PAIRS = {("N1", "N2"), ("N2", "N3")}

    prev_target_code = "OFF"
    prev_other_code = "OFF"

    for day in days_in_year:
        persons_today = day.get("persons", [])

        date = day["date"]
        weekday_name = day["weekday_name"]

        their_position = other_person_id if other_key is None else holders.position_at(other_key, date)
        other = None if their_position is None else _find_person_in_day(persons_today, their_position)
        other_code = other["shift"].code if other and other.get("shift") else "OFF"

        # Days the viewed person held no position, or sat at the coworker's own
        # position, produce no handover. Keep them masked OFF in the carried
        # forward state so no cross-day handover crosses the edge.
        my_position = _position_on(date, segments, target_person_id)
        if my_position is None or their_position is None or my_position == their_position:
            prev_target_code = "OFF"
            prev_other_code = other_code
            continue

        target = _find_person_in_day(persons_today, my_position)
        target_code = target["shift"].code if target and target.get("shift") else "OFF"

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
            # Person has OT, target has a regular shift. Match on time only: the
            # overtime shift replaced their rotation shift but is not necessarily
            # worked at its hours, so original_shift says nothing about whether
            # the two are on the floor together.
            if target_start and target_end:
                # Check time overlap
                other_start = person_data.get("start")
                other_end = person_data.get("end")
                if other_start and other_end:
                    # Check if shifts overlap significantly (at least 4 hours)
                    overlap = _calculate_overlap_hours(target_start, target_end, other_start, other_end)
                    if overlap >= 4.0:
                        is_match = True
        else:
            # Regular work shift (N1, N2, N3) - use actual shift for matching.
            # original_shift is not used here: if this person has a swap, actual_shift
            # already reflects the swapped shift, and original_shift would be their
            # rotation-based shift (e.g. OFF), which would give a false non-match.
            if actual_shift.code == target_shift_code:
                is_match = True

        if is_match:
            coworkers.append(
                {
                    "id": person_id,
                    "name": person_data.get("person_name", ""),
                }
            )

    # Sort by person_id and return just names. person_id may be an int (rotation
    # position) or a string (substitute, e.g. "sub-3"), so sort ints first among
    # themselves, then strings, to avoid comparing str with int.
    coworkers.sort(key=lambda x: (isinstance(x["id"], str), x["id"]))
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
