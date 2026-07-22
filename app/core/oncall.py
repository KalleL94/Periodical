# app/core/oncall.py
"""
On-call (beredskap) compensation calculations.

This module implements REPLACEMENT logic for on-call pay, which differs from OB's
ADDITIVE logic:

ADDITIVE (OB system):
    - Multiple OB rules can apply to the same time period
    - OB1 + OB2 + OB3 can all contribute to the same hours
    - Each OB code tracks its own hours independently

REPLACEMENT (On-call system):
    - Only ONE rule applies to each time segment
    - Higher priority rules REPLACE lower priority rules completely
    - Priority order: OC_NATIONALDAGEN (6) > OC_SPECIAL (5) > OC_HOLIDAY (4) >
                      OC_HOLIDAY_EVE (3) > OC_WEEKEND (2) > OC_WEEKDAY (1)
    - Result: each hour of an on-call shift is compensated at exactly one rate

Example:
    A 24-hour on-call shift on Christmas Day, which is a Friday:
    - 00:00-24:00: OC_SPECIAL (storhelg) replaces OC_WEEKDAY and OC_WEEKEND outright
    Total: 24 h at 192 kr/h
"""

import datetime
from functools import lru_cache

from .holidays import (
    alla_helgons_dag,
    annandagpask,
    easter_sunday,
    first_weekday_after,
    forsta_maj,
    julafton,
    kristi_himmelsfardsdag,
    langfredagen,
    midsommarafton,
    nationaldagen,
    nyarsafton,
    nyarsdagen,
    skartorsdagen,
    trettondagen,
)
from .models import OnCallRule
from .storage import load_oncall_rules
from .time_utils import subtract_covered

# Load base rules from JSON config
oncall_rules_base = load_oncall_rules()

# Fixed hourly compensation in SEK. data/oncall_rules.json carries the same numbers
# on its OC_HOLIDAY and OC_SPECIAL entries, but those entries have no days and no
# specific_dates, so they never match anything: the rules generated below are what
# actually pays out, and these constants are what they pay.
RATE_STORHELG = 192
RATE_RED_DAY = 112

# Replacement priorities. The highest-priority rule covering an hour wins outright.
PRIORITY_NATIONALDAGEN = 6
PRIORITY_STORHELG = 5
PRIORITY_RED_DAY = 4


def _generated_rule(
    code: str,
    label: str,
    date: datetime.date,
    start: str,
    end: str,
    rate: int,
    priority: int,
) -> OnCallRule:
    """One generated single-date rule. end "00:00" means midnight the following day."""
    return OnCallRule(
        code=code,
        label=label,
        specific_dates=[date.isoformat()],
        start_time=start,
        end_time=end,
        fixed_hourly_rate=rate,
        priority=priority,
        generated=True,
    )


def _storhelg(date: datetime.date, start: str = "00:00", end: str = "00:00") -> OnCallRule:
    """Storhelg day at 192 kr/h. Defaults to the full day."""
    return _generated_rule("OC_SPECIAL", "Beredskap storhelg", date, start, end, RATE_STORHELG, PRIORITY_STORHELG)


def _red_day(date: datetime.date, start: str = "00:00", end: str = "24:00") -> OnCallRule:
    """Public holiday at 112 kr/h. Defaults to the full day."""
    return _generated_rule("OC_HOLIDAY", "Beredskap röd dag", date, start, end, RATE_RED_DAY, PRIORITY_RED_DAY)


def _nationaldagen_rule(date: datetime.date, start: str = "00:00", end: str = "00:00") -> OnCallRule:
    """Nationaldagen block day. Red day rate, but its own code and top priority."""
    return _generated_rule(
        "OC_NATIONALDAGEN",
        "Beredskap Nationaldagen",
        date,
        start,
        end,
        RATE_RED_DAY,
        PRIORITY_NATIONALDAGEN,
    )


def _get_holidays_for_year(year: int) -> set[datetime.date]:
    """
    Return set of all Swedish public holidays for the year.

    Includes both fixed holidays (Jan 1, May 1, Jun 6, Dec 24-26) and
    moveable holidays (Easter-related, Midsummer, All Saints).
    """
    holidays = set()

    # Fixed holidays
    holidays.add(nyarsdagen(year))  # 1 jan
    holidays.add(trettondagen(year))  # 6 jan
    holidays.add(forsta_maj(year))  # 1 maj
    holidays.add(nationaldagen(year))  # 6 jun
    holidays.add(julafton(year))  # 24 dec
    holidays.add(datetime.date(year, 12, 25))  # Juldagen
    holidays.add(datetime.date(year, 12, 26))  # Annandag jul
    holidays.add(nyarsafton(year))  # 31 dec

    # Easter-related (moveable)
    holidays.add(langfredagen(year))  # Good Friday
    holidays.add(easter_sunday(year))  # Easter Sunday
    holidays.add(annandagpask(year))  # Easter Monday
    holidays.add(skartorsdagen(year))  # Maundy Thursday (evening)

    # Ascension Day
    holidays.add(kristi_himmelsfardsdag(year))

    # NOTE: Pentecost (Pingst) is NOT included - gets normal weekend rate (97 kr)
    # Pingstafton is always Saturday, Pingstdagen is always Sunday

    # Midsummer (Friday-Saturday between Jun 19-25)
    holidays.add(midsommarafton(year))
    midsummer_day = midsommarafton(year) + datetime.timedelta(days=1)
    holidays.add(midsummer_day)

    # All Saints Day (Saturday between Oct 31 - Nov 6)
    holidays.add(alla_helgons_dag(year))

    return holidays


def _get_nationaldagen_block(year: int) -> set[datetime.date]:
    """
    Return set of dates for Nationaldagen block.

    Nationaldagen (June 6) is treated like storhelg timewise but with red day rate (112 kr).
    Block: From 17:00 day before to 07:00 first weekday after.
    """
    nationaldagen_block = set()

    nat_day = nationaldagen(year)
    # Start from day before at 17:00 (add that day)
    day_before = nat_day - datetime.timedelta(days=1)
    nationaldagen_block.add(day_before)

    # Add Nationaldagen itself
    nationaldagen_block.add(nat_day)

    # Add all days until first weekday after Nationaldagen
    nat_end = first_weekday_after(nat_day)
    current = nat_day + datetime.timedelta(days=1)
    while current < nat_end:
        nationaldagen_block.add(current)
        current += datetime.timedelta(days=1)

    return nationaldagen_block


def _get_storhelg_dates_for_year(year: int) -> set[datetime.date]:
    """
    Return set of dates that qualify as 'storhelg' (major holidays).

    These get the highest on-call rate (OC_SPECIAL).
    Includes: Christmas block, New Year block, Easter block, Midsummer block.
    Each block extends to the first weekday after.
    """
    storhelg = set()

    # Christmas block: Dec 24 → first weekday after Dec 26
    christmas_start = julafton(year)
    christmas_end = first_weekday_after(datetime.date(year, 12, 26))
    d = christmas_start
    while d < christmas_end:
        storhelg.add(d)
        d += datetime.timedelta(days=1)

    # New Year block: Dec 31 (opened at 17:00 on Dec 30) → first weekday after Jan 1
    # We add both Dec 31 and all days until first weekday
    storhelg.add(nyarsafton(year))
    new_year_end = first_weekday_after(nyarsdagen(year + 1))
    d = nyarsdagen(year + 1)
    while d < new_year_end:
        storhelg.add(d)
        d += datetime.timedelta(days=1)

    # Also handle incoming New Year from previous year
    new_year_end_this = first_weekday_after(nyarsdagen(year))
    d = nyarsdagen(year)
    while d < new_year_end_this:
        storhelg.add(d)
        d += datetime.timedelta(days=1)

    # Easter block: Skärtorsdag 17:00 → first weekday after Annandag påsk
    easter_start = skartorsdagen(year)
    easter_end = first_weekday_after(annandagpask(year))
    d = easter_start
    while d < easter_end:
        storhelg.add(d)
        d += datetime.timedelta(days=1)

    # Midsummer block: Midsommarafton → first weekday after Midsommardagen
    midsummer_start = midsommarafton(year)
    midsummer_end = first_weekday_after(midsommarafton(year) + datetime.timedelta(days=1))
    d = midsummer_start
    while d < midsummer_end:
        storhelg.add(d)
        d += datetime.timedelta(days=1)

    # NOTE: Pingst is NOT included as storhelg for on-call compensation

    return storhelg


def build_oncall_rules_for_year(year: int) -> list[OnCallRule]:
    """
    Build complete list of on-call rules for a specific year.

    Combines:
    1. Static rules from oncall_rules.json (weekday-based)
    2. Dynamically generated rules for specific holiday dates

    Emission order is part of the contract: calculate_oncall_pay_for_period keeps the
    first rule it sees per code. See tests/test_oncall_rules_golden.py.
    """
    rules: list[OnCallRule] = list(oncall_rules_base)

    holidays = _get_holidays_for_year(year)
    storhelg_dates = _get_storhelg_dates_for_year(year)

    # Nationaldagen gets the same time windows a red day would get, but its own code
    # and a priority above storhelg so nothing else can claim those hours.
    nationaldagen_block = _get_nationaldagen_block(year)
    nat_day = nationaldagen(year)
    nat_eve = nat_day - datetime.timedelta(days=1)
    nat_first_weekday = first_weekday_after(nat_day)

    for date in sorted(nationaldagen_block):
        # The eve opens at 17:00; Nationaldagen itself and any day up to the first
        # weekday is a full day.
        rules.append(_nationaldagen_rule(date, start="17:00" if date == nat_eve else "00:00"))

    if nat_first_weekday not in nationaldagen_block:
        rules.append(_nationaldagen_rule(nat_first_weekday, end="07:00"))

    # Each storhelg block opens at 17:00 on the day before its first full day.
    for eve in (
        julafton(year) - datetime.timedelta(days=1),
        midsommarafton(year) - datetime.timedelta(days=1),
        nyarsafton(year) - datetime.timedelta(days=1),
    ):
        if eve not in storhelg_dates:
            rules.append(_storhelg(eve, start="17:00"))

    for date in sorted(storhelg_dates):
        # Skärtorsdag is the one storhelg day that is itself the opening day, so it
        # starts at 17:00 rather than getting a separate eve rule above.
        rules.append(_storhelg(date, start="17:00" if date == skartorsdagen(year) else "00:00"))

    # Each storhelg block runs until 07:00 on the first weekday after it.
    for block_end in (
        first_weekday_after(datetime.date(year, 12, 26)),
        first_weekday_after(nyarsdagen(year)),
        first_weekday_after(annandagpask(year)),
        first_weekday_after(midsommarafton(year) + datetime.timedelta(days=1)),
    ):
        if block_end not in storhelg_dates:
            rules.append(_storhelg(block_end, end="07:00"))

    # Regular holidays: everything the storhelg and Nationaldagen blocks did not claim.
    claimed = holidays | storhelg_dates | nationaldagen_block
    for date in sorted(holidays - storhelg_dates - nationaldagen_block):
        rules.append(_red_day(date))

        eve = date - datetime.timedelta(days=1)
        if eve not in claimed:
            rules.append(_red_day(eve, start="17:00"))

        if date.weekday() >= 4:
            # Friday to Sunday: the block runs over the weekend, like a storhelg block.
            block_end = first_weekday_after(date)
            current = date + datetime.timedelta(days=1)
            while current < block_end:
                if current not in claimed:
                    rules.append(_red_day(current))
                current += datetime.timedelta(days=1)
        else:
            block_end = date + datetime.timedelta(days=1)

        if block_end not in claimed:
            rules.append(_red_day(block_end, end="07:00"))

    return rules


@lru_cache(maxsize=10)
def _cached_oncall_rules(year: int) -> list[OnCallRule]:
    """Cached version of build_oncall_rules_for_year."""
    return build_oncall_rules_for_year(year)


def _select_oncall_rules_for_date(
    dt: datetime.date,
    oncall_rules: list[OnCallRule],
) -> list[OnCallRule]:
    """
    Select on-call rules that apply to a specific datetime.

    Matches rules by:
    - Weekday (dt.weekday() in rule.days)
    - OR specific date (dt.date().isoformat() in rule.specific_dates)

    Returns list of applicable rules (will be sorted by priority for processing).
    """
    weekday = dt.weekday()
    # Fixed to handle both date and datetime objects
    if isinstance(dt, datetime.datetime):
        date_iso = dt.date().isoformat()
    else:
        date_iso = dt.isoformat()

    applicable = []
    for rule in oncall_rules:
        match = False

        if rule.days and weekday in rule.days:
            match = True

        if rule.specific_dates and date_iso in rule.specific_dates:
            match = True

        if match:
            applicable.append(rule)

    return applicable


def _parse_time(time_str: str) -> tuple[int, int]:
    """Parse 'HH:MM' string to (hour, minute) tuple. Handles '24:00'."""
    if time_str == "24:00":
        return (24, 0)
    h, m = map(int, time_str.split(":"))
    return (h, m)


def _get_rule_intervals_for_shift(
    rule: OnCallRule,
    shift_start: datetime.datetime,
    shift_end: datetime.datetime,
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """
    Calculate time intervals where a rule applies within a shift period.

    Handles:
    - Rules that span midnight (spans_to_next_day or end <= start)
    - Multiple days within shift span
    - '24:00' as next day midnight

    Returns list of (start, end) datetime tuples.
    """
    intervals = []

    rule_start_h, rule_start_m = _parse_time(rule.start_time)
    rule_end_h, rule_end_m = _parse_time(rule.end_time)

    # Handle 24:00 as indicator of midnight
    ends_at_midnight = rule_end_h == 24
    if ends_at_midnight:
        rule_end_h = 0

    # Determine if rule spans midnight
    spans_midnight = rule.spans_to_next_day or (
        datetime.time(rule_end_h, rule_end_m) <= datetime.time(rule_start_h, rule_start_m) and not ends_at_midnight
    )

    # Iterate through each day in the shift span
    current_date = shift_start.date()
    end_date = shift_end.date()
    if shift_end.time() == datetime.time(0, 0):
        # If shift ends at midnight, don't include that day
        end_date = shift_end.date() - datetime.timedelta(days=1)

    while current_date <= end_date:
        # Check if rule applies to this date
        date_applies = False

        if rule.days and current_date.weekday() in rule.days:
            date_applies = True
        if rule.specific_dates and current_date.isoformat() in rule.specific_dates:
            date_applies = True

        if date_applies:
            # Create rule interval for this date
            interval_start = datetime.datetime.combine(current_date, datetime.time(rule_start_h, rule_start_m))

            if ends_at_midnight:
                interval_end = datetime.datetime.combine(current_date + datetime.timedelta(days=1), datetime.time(0, 0))
            elif spans_midnight:
                interval_end = datetime.datetime.combine(
                    current_date + datetime.timedelta(days=1), datetime.time(rule_end_h, rule_end_m)
                )
            else:
                interval_end = datetime.datetime.combine(current_date, datetime.time(rule_end_h, rule_end_m))

            # Clip to shift boundaries
            clipped_start = max(interval_start, shift_start)
            clipped_end = min(interval_end, shift_end)

            if clipped_start < clipped_end:
                intervals.append((clipped_start, clipped_end))

        current_date += datetime.timedelta(days=1)

    return intervals


def calculate_oncall_pay(
    date: datetime.date,
    monthly_salary: int,
    oncall_rules: list[OnCallRule] | None = None,
    rate_overrides: dict[str, int | float] | None = None,
    excluded_intervals: list[tuple[datetime.datetime, datetime.datetime]] | None = None,
) -> dict:
    """
    Calculate on-call compensation for a 24-hour shift.

    On-call shifts run from 00:00 on the given date to 00:00 the next day.

    Args:
        date: Date when on-call shift starts (at 00:00)
        monthly_salary: Employee's monthly salary for rate calculation
        oncall_rules: Optional list of rules (if None, uses cached rules for year)
        excluded_intervals: Time intervals to treat as already covered (e.g. an OT shift
            on the same day) so they are excluded from on-call pay.

    Returns:
        Dict with:
        - total_pay: Total compensation in SEK
        - breakdown: Dict of {code: {hours, rate, pay}} for each rule that applied
        - total_hours: Should always be 24.0 for a full on-call shift
        - effective_rate: Weighted average rate divisor
        - segments: List of time segments with their applied rules
    """
    # Define shift period: 00:00 to 00:00 next day
    shift_start = datetime.datetime.combine(date, datetime.time(0, 0))
    shift_end = datetime.datetime.combine(date + datetime.timedelta(days=1), datetime.time(0, 0))

    # Get rules if not provided
    if oncall_rules is None:
        oncall_rules = _cached_oncall_rules(date.year)

    # Sort rules by priority (highest first)
    sorted_rules = sorted(oncall_rules, key=lambda r: r.priority, reverse=True)

    # Track covered time and results; pre-populate with any excluded intervals (e.g. OT)
    covered: list[tuple[datetime.datetime, datetime.datetime]] = list(excluded_intervals or [])
    breakdown: dict[str, dict] = {}
    segments: list[dict] = []

    # Process rules in priority order
    for rule in sorted_rules:
        # Get intervals where this rule applies
        intervals = _get_rule_intervals_for_shift(rule, shift_start, shift_end)

        for interval_start, interval_end in intervals:
            # Find uncovered portions of this interval
            uncovered = subtract_covered(interval_start, interval_end, covered)

            for unc_start, unc_end in uncovered:
                hours = (unc_end - unc_start).total_seconds() / 3600.0

                if hours > 0:
                    # Calculate pay for this segment (check per-user override first)
                    overrides = rate_overrides or {}
                    override_rate = overrides.get(rule.code)
                    if override_rate is not None:
                        segment_pay = override_rate * hours
                        rate_display = override_rate
                    elif rule.fixed_hourly_rate is not None:
                        # New system: fixed SEK per hour
                        segment_pay = rule.fixed_hourly_rate * hours
                        rate_display = rule.fixed_hourly_rate
                    else:
                        # Legacy system: monthly_salary / rate
                        segment_pay = (monthly_salary / rule.rate) * hours
                        rate_display = rule.rate

                    # Track in breakdown
                    if rule.code not in breakdown:
                        breakdown[rule.code] = {
                            "hours": 0.0,
                            "rate": rate_display,
                            "pay": 0.0,
                            "label": rule.label,
                        }
                    breakdown[rule.code]["hours"] += hours
                    breakdown[rule.code]["pay"] += segment_pay

                    # Track segment
                    segments.append(
                        {
                            "start": unc_start,
                            "end": unc_end,
                            "hours": hours,
                            "code": rule.code,
                            "rate": rate_display,
                            "pay": segment_pay,
                        }
                    )

                    # Mark as covered
                    covered.append((unc_start, unc_end))

    # Calculate totals
    total_pay = sum(b["pay"] for b in breakdown.values())
    total_hours = sum(b["hours"] for b in breakdown.values())

    # Calculate effective rate (weighted average)
    if total_hours > 0:
        weighted_rate = sum(b["rate"] * b["hours"] for b in breakdown.values()) / total_hours
    else:
        weighted_rate = 0

    return {
        "date": date,
        "total_pay": round(total_pay, 2),
        "total_hours": round(total_hours, 2),
        "effective_rate": round(weighted_rate, 1),
        "breakdown": breakdown,
        "segments": sorted(segments, key=lambda s: s["start"]),
    }


def calculate_oncall_pay_for_period(
    start_dt: datetime.datetime,
    end_dt: datetime.datetime,
    monthly_salary: int,
    oncall_rules: list[OnCallRule],
    rate_overrides: dict[str, int | float] | None = None,
) -> dict:
    """
    Calculate on-call compensation for a specific time period.

    This is used when an OT shift interrupts an OC shift, so we need to
    calculate OC pay only for the portion before the OT shift starts.

    Args:
        start_dt: Period start (e.g., 00:00 on the OC day)
        end_dt: Period end (e.g., 14:00 when OT begins)
        monthly_salary: Employee's monthly salary
        oncall_rules: List of applicable on-call rules

    Returns:
        Dict with:
            - total_pay: Total compensation for the period
            - breakdown: Dict of {code: {hours, rate, pay, label}}
            - total_hours: Total hours in period
            - effective_rate: Weighted average divisor
    """
    # Calculate total hours in period
    period_hours = (end_dt - start_dt).total_seconds() / 3600.0

    if period_hours <= 0:
        return {"total_pay": 0.0, "breakdown": {}, "total_hours": 0.0, "effective_rate": 0}

    # Get applicable rules for the dates in this period
    applicable_rules = []
    current_date = start_dt.date()
    end_date = end_dt.date()

    while current_date <= end_date:
        date_dt = datetime.datetime.combine(current_date, datetime.time(0, 0))
        rules = _select_oncall_rules_for_date(date_dt, oncall_rules)
        applicable_rules.extend(rules)
        current_date += datetime.timedelta(days=1)

    # Remove duplicates
    unique_rules = []
    seen_codes = set()
    for rule in applicable_rules:
        if rule.code not in seen_codes:
            unique_rules.append(rule)
            seen_codes.add(rule.code)

    # Sort by priority (highest first)
    sorted_rules = sorted(unique_rules, key=lambda r: r.priority, reverse=True)

    # Track which time segments are covered and by which rule
    breakdown = {}
    covered_intervals = []
    total_pay = 0.0

    for rule in sorted_rules:
        # Get intervals where this rule applies within our period
        rule_intervals = _get_rule_intervals_for_shift(rule, start_dt, end_dt)

        # Find uncovered portions
        for interval_start, interval_end in rule_intervals:
            # Constrain to our period
            interval_start = max(interval_start, start_dt)
            interval_end = min(interval_end, end_dt)

            if interval_start >= interval_end:
                continue

            # Calculate uncovered portions
            uncovered = subtract_covered(interval_start, interval_end, covered_intervals)

            for uncov_start, uncov_end in uncovered:
                # Calculate hours and pay for this segment
                segment_hours = (uncov_end - uncov_start).total_seconds() / 3600.0

                # Calculate pay based on rule type (check per-user override first)
                overrides = rate_overrides or {}
                override_rate = overrides.get(rule.code)
                if override_rate is not None:
                    segment_pay = override_rate * segment_hours
                    rate_display = override_rate
                elif rule.fixed_hourly_rate is not None:
                    # New system: fixed SEK per hour
                    segment_pay = rule.fixed_hourly_rate * segment_hours
                    rate_display = rule.fixed_hourly_rate
                else:
                    # Legacy system: monthly_salary / rate
                    segment_pay = (monthly_salary / rule.rate) * segment_hours
                    rate_display = rule.rate

                # Add to breakdown
                if rule.code not in breakdown:
                    breakdown[rule.code] = {
                        "hours": 0.0,
                        "rate": rate_display,
                        "pay": 0.0,
                        "label": rule.label,
                    }

                breakdown[rule.code]["hours"] += segment_hours
                breakdown[rule.code]["pay"] += segment_pay
                total_pay += segment_pay

                # Mark as covered
                covered_intervals.append((uncov_start, uncov_end))

    # Calculate effective rate
    effective_rate = (monthly_salary * period_hours / total_pay) if total_pay > 0 else 0

    return {
        "total_pay": total_pay,
        "breakdown": breakdown,
        "total_hours": period_hours,
        "effective_rate": effective_rate,
    }


def apply_oncall_hours_override(
    hour_overrides: dict[str, float],
    original_breakdown: dict,
    monthly_salary: int,
    oncall_rules: list,
    rate_overrides: dict | None = None,
) -> tuple[float, dict]:
    """Rebuild oncall pay and details from manually overridden hours per type code.

    For types present in original_breakdown the per-hour rate is derived from
    the original result. For new types the rate is looked up from oncall_rules.
    Returns (total_pay, details_dict).
    """
    new_breakdown: dict[str, dict] = {}
    overrides = rate_overrides or {}

    for code, raw_hours in hour_overrides.items():
        h = float(raw_hours)
        if h <= 0:
            continue

        # Try to derive per-hour rate from original breakdown
        orig = original_breakdown.get(code, {})
        if orig.get("hours", 0) > 0:
            pay_per_hour = orig["pay"] / orig["hours"]
            label = orig.get("label", code)
            rate_display = orig.get("rate", pay_per_hour)
        else:
            # Look up rate from rules
            rule = next((r for r in oncall_rules if r.code == code), None)
            if rule is None:
                continue
            override_val = overrides.get(code)
            if override_val is not None:
                pay_per_hour = float(override_val)
            elif rule.fixed_hourly_rate is not None:
                pay_per_hour = float(rule.fixed_hourly_rate)
            else:
                pay_per_hour = monthly_salary / rule.rate
            label = rule.label
            rate_display = pay_per_hour

        pay = round(h * pay_per_hour, 2)
        new_breakdown[code] = {"hours": h, "rate": rate_display, "pay": pay, "label": label}

    total_pay = round(sum(b["pay"] for b in new_breakdown.values()), 2)
    total_hours = round(sum(b["hours"] for b in new_breakdown.values()), 2)

    return total_pay, {
        "total_pay": total_pay,
        "total_hours": total_hours,
        "effective_rate": 0.0,
        "breakdown": new_breakdown,
        "segments": [],
        "is_overridden": True,
    }


def clear_oncall_cache():
    """Clear cached on-call rules (call after rule changes)."""
    _cached_oncall_rules.cache_clear()
