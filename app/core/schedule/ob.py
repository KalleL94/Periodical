"""OB-tilläggsberäkningar."""

import datetime
from functools import lru_cache

from app.core.constants import OB_PRIORITY_BY_CODE, OB_PRIORITY_DEFAULT
from app.core.models import ObRule
from app.core.storage import load_ob_rules

_ob_rules: list[ObRule] | None = None


def get_ob_rules() -> list[ObRule]:
    """Hämtar bas-OB-regler."""
    global _ob_rules
    if _ob_rules is None:
        _ob_rules = load_ob_rules()
    return _ob_rules


@lru_cache(maxsize=10)
def get_special_rules_for_year(year: int) -> list[ObRule]:
    """Cachad hämtning av specialregler (helgdagar) för ett år."""
    from .holidays_ob import build_special_ob_rules_for_year

    return build_special_ob_rules_for_year(year)


def get_combined_rules_for_year(year: int) -> list[ObRule]:
    """Returnerar alla OB-regler (bas + special) för ett år."""
    return list(get_ob_rules()) + get_special_rules_for_year(year)


def calculate_ob_hours(
    start_dt: datetime.datetime,
    end_dt: datetime.datetime,
    rules: list[ObRule],
) -> dict[str, float]:
    """
    Räknar OB-timmar per kod mellan start och slut.

    Prioritet: OB5 > OB4 > övriga (ingen dubbel räkning av samma tid)

    Args:
        start_dt: Passens starttid
        end_dt: Passens sluttid
        rules: Lista av OB-regler att tillämpa

    Returns:
        Dict med OB-kod -> timmar
    """
    ob_totals = {rule.code: 0.0 for rule in rules}

    if not start_dt or not end_dt or end_dt <= start_dt:
        return ob_totals

    current = start_dt
    while current < end_dt:
        # Begränsa till dagens slut
        day_end = datetime.datetime.combine(
            current.date() + datetime.timedelta(days=1),
            datetime.time(0, 0),
        )
        segment_end = min(end_dt, day_end)

        # Hämta och prioritera dagens regler
        todays_rules = _select_rules_for_date(current, rules)
        sorted_rules = sorted(todays_rules, key=_rule_priority, reverse=True)

        covered: list[tuple[datetime.datetime, datetime.datetime]] = []

        for rule in sorted_rules:
            ob_start, ob_end = _rule_interval_for_day(rule, current)

            overlap_start = max(current, ob_start)
            overlap_end = min(segment_end, ob_end)

            if overlap_end <= overlap_start:
                continue

            # Subtrahera redan täckta intervall
            uncovered = _subtract_covered(overlap_start, overlap_end, covered)

            for ustart, uend in uncovered:
                hours = (uend - ustart).total_seconds() / 3600.0
                ob_totals[rule.code] += hours
                covered.append((ustart, uend))

        current = segment_end

    return ob_totals


def calculate_ob_pay(
    start_dt: datetime.datetime,
    end_dt: datetime.datetime,
    rules: list[ObRule],
    monthly_salary: int,
) -> dict[str, float]:
    """
    Beräknar OB-ersättning per kod.

    Args:
        start_dt: Passens starttid
        end_dt: Passens sluttid
        rules: Lista av OB-regler
        monthly_salary: Månadslön i SEK

    Returns:
        Dict med OB-kod -> ersättning i SEK
    """
    hours = calculate_ob_hours(start_dt, end_dt, rules)

    pays = {}
    for rule in rules:
        h = hours.get(rule.code, 0.0)
        rate = getattr(rule, "rate", None)
        if rate and h > 0:
            pays[rule.code] = h * (monthly_salary / float(rate))
        else:
            pays[rule.code] = 0.0

    return pays


# === Privata hjälpfunktioner ===


def _rule_priority(rule: ObRule) -> int:
    """Returnerar prioritet för en OB-regel."""
    return OB_PRIORITY_BY_CODE.get(rule.code, OB_PRIORITY_DEFAULT)


def _select_rules_for_date(
    dt: datetime.datetime,
    rules: list[ObRule],
) -> list[ObRule]:
    """Väljer regler som gäller för ett specifikt datum."""
    weekday = dt.weekday()
    date_iso = dt.date().isoformat()

    matching = []
    for rule in rules:
        match = False

        # Matcha på veckodag
        if getattr(rule, "days", None) and weekday in rule.days:
            match = True

        # Matcha på specifikt datum
        if not match and getattr(rule, "specific_date", None) == date_iso:
            match = True

        # Matcha på lista av datum
        if not match:
            specific_dates = getattr(rule, "specific_dates", None)
            if specific_dates and date_iso in specific_dates:
                match = True

        if match:
            matching.append(rule)

    return matching


"""OB-tilläggsberäkningar."""

# === Publika hjälpfunktioner ===


def select_ob_rules_for_date(
    dt: datetime.datetime,
    rules: list[ObRule],
) -> list[ObRule]:
    """
    Väljer regler som gäller för ett specifikt datum.

    Publik funktion för användning i routes.
    """
    weekday = dt.weekday()
    date_iso = dt.date().isoformat()

    matching = []
    for rule in rules:
        match = False

        if getattr(rule, "days", None) and weekday in rule.days:
            match = True

        if not match and getattr(rule, "specific_date", None) == date_iso:
            match = True

        if not match:
            specific_dates = getattr(rule, "specific_dates", None)
            if specific_dates and date_iso in specific_dates:
                match = True

        if match:
            matching.append(rule)

    return matching


# === Privata hjälpfunktioner ===


def _rule_priority(rule: ObRule) -> int:
    """Returnerar prioritet för en OB-regel."""
    return OB_PRIORITY_BY_CODE.get(rule.code, OB_PRIORITY_DEFAULT)


def _select_rules_for_date(
    dt: datetime.datetime,
    rules: list[ObRule],
) -> list[ObRule]:
    """Väljer regler som gäller för ett specifikt datum."""
    weekday = dt.weekday()
    date_iso = dt.date().isoformat()

    matching = []
    for rule in rules:
        match = False

        # Matcha på veckodag
        if getattr(rule, "days", None) and weekday in rule.days:
            match = True

        # Matcha på specifikt datum
        if not match and getattr(rule, "specific_date", None) == date_iso:
            match = True

        # Matcha på lista av datum
        if not match:
            specific_dates = getattr(rule, "specific_dates", None)
            if specific_dates and date_iso in specific_dates:
                match = True

        if match:
            matching.append(rule)

    return matching


def _rule_interval_for_day(
    rule: ObRule,
    dt: datetime.datetime,
) -> tuple[datetime.datetime, datetime.datetime]:
    """Bygger tidsintervall för en regel på en specifik dag."""
    start_h, start_m = map(int, rule.start_time.split(":"))
    end_h, end_m = map(int, rule.end_time.split(":"))

    base_date = dt.date()
    ob_start = datetime.datetime(base_date.year, base_date.month, base_date.day, start_h, start_m)

    if rule.end_time == "24:00":
        ob_end = datetime.datetime.combine(
            base_date + datetime.timedelta(days=1),
            datetime.time(0, 0),
        )
    else:
        ob_end = datetime.datetime(base_date.year, base_date.month, base_date.day, end_h, end_m)

    return ob_start, ob_end


def _subtract_covered(
    start: datetime.datetime,
    end: datetime.datetime,
    covered: list[tuple[datetime.datetime, datetime.datetime]],
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """Returnerar otäckta intervall efter subtraktion av redan täckta."""
    result = []
    cursor = start

    for cov_start, cov_end in sorted(covered):
        if cov_end <= cursor or cov_start >= end:
            continue
        if cov_start > cursor:
            result.append((cursor, min(cov_start, end)))
        cursor = max(cursor, cov_end)

    if cursor < end:
        result.append((cursor, end))

    return result
