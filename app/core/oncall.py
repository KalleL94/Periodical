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
    - Priority order: OC_SPECIAL (6) > OC_HOLIDAY (5) > OC_HOLIDAY_EVE (4) >
                      OC_WEEKEND (3) > OC_FRIDAY_NIGHT (2) > OC_DEFAULT (1)
    - Result: each hour of an on-call shift is compensated at exactly one rate

Example:
    A 24-hour on-call shift on Christmas Eve (Saturday):
    - 06:00-07:00: OC_SPECIAL (storhelg) - highest priority
    - 07:00-06:00: OC_SPECIAL continues (holiday block extends through weekend)
    Total: 24h at rate 250 → månadslön / 250 = daily pay
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

# Load base rules from JSON config
oncall_rules_base = load_oncall_rules()


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
    holidays.add(langfredagen(year))  # Långfredag
    holidays.add(easter_sunday(year))  # Påskdagen
    holidays.add(annandagpask(year))  # Annandag påsk
    holidays.add(skartorsdagen(year))  # Skärtorsdag (afton)

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

    # New Year block: Dec 31 18:00 → first weekday after Jan 1
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

    # Easter block: Skärtorsdag 18:00 → first weekday after Annandag påsk
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

    Returns list sorted by priority (used for replacement logic).
    """
    rules: list[OnCallRule] = []

    for rule in oncall_rules_base:
        rules.append(rule)

    holidays = _get_holidays_for_year(year)
    storhelg_dates = _get_storhelg_dates_for_year(year)

    # Nationaldagen gets special treatment: extended like storhelg but at red day rate (112 kr)
    nationaldagen_block = _get_nationaldagen_block(year)
    nat_day = nationaldagen(year)

    # Get first weekday after Nationaldagen
    first_weekday = first_weekday_after(nat_day)

    for date in sorted(nationaldagen_block):
        # Determine start and end times based on which day it is
        if date == nat_day - datetime.timedelta(days=1):
            # Day before Nationaldagen: start at 17:00
            start_time = "17:00"
            end_time = "00:00"
            specific_dates = [date.isoformat()]
            spans = False
        elif date == first_weekday - datetime.timedelta(days=1):
            # Last day before first weekday: full day (00:00-24:00)
            start_time = "00:00"
            end_time = "00:00"
            specific_dates = [date.isoformat()]
            spans = False
        else:
            # Nationaldagen itself and intermediate days: full day
            start_time = "00:00"
            end_time = "00:00"
            specific_dates = [date.isoformat()]
            spans = False

        rules.append(
            OnCallRule(
                code="OC_NATIONALDAGEN",
                label="Beredskap Nationaldagen",
                specific_dates=specific_dates,
                start_time=start_time,
                end_time=end_time,
                fixed_hourly_rate=112,
                priority=6,  # Higher than everything including storhelg to override weekend rules
                generated=True,
                spans_to_next_day=spans,
            )
        )

    # Add separate rule for first weekday morning (00:00-07:00)
    if first_weekday not in nationaldagen_block:
        rules.append(
            OnCallRule(
                code="OC_NATIONALDAGEN",
                label="Beredskap Nationaldagen",
                specific_dates=[first_weekday.isoformat()],
                start_time="00:00",
                end_time="07:00",
                fixed_hourly_rate=112,
                priority=6,
                generated=True,
                spans_to_next_day=False,
            )
        )

    # Add day-before rules for julafton and midsommarafton (start at 17:00)
    julafton_date = julafton(year)
    day_before_jul = julafton_date - datetime.timedelta(days=1)
    if day_before_jul not in storhelg_dates:
        rules.append(
            OnCallRule(
                code="OC_SPECIAL",
                label="Beredskap storhelg",
                specific_dates=[day_before_jul.isoformat()],
                start_time="17:00",
                end_time="00:00",
                fixed_hourly_rate=192,
                priority=5,
                generated=True,
            )
        )

    midsommar_date = midsommarafton(year)
    day_before_midsommar = midsommar_date - datetime.timedelta(days=1)
    if day_before_midsommar not in storhelg_dates:
        rules.append(
            OnCallRule(
                code="OC_SPECIAL",
                label="Beredskap storhelg",
                specific_dates=[day_before_midsommar.isoformat()],
                start_time="17:00",
                end_time="00:00",
                fixed_hourly_rate=192,
                priority=5,
                generated=True,
            )
        )

    # Add day-before rule for nyårsafton (30/12 from 17:00)
    nyarsafton_date = nyarsafton(year)
    day_before_nyar = nyarsafton_date - datetime.timedelta(days=1)
    if day_before_nyar not in storhelg_dates:
        rules.append(
            OnCallRule(
                code="OC_SPECIAL",
                label="Beredskap storhelg",
                specific_dates=[day_before_nyar.isoformat()],
                start_time="17:00",
                end_time="00:00",
                fixed_hourly_rate=192,
                priority=5,
                generated=True,
            )
        )

    for date in sorted(storhelg_dates):
        if date == skartorsdagen(year):
            start_time = "17:00"  # Skärtorsdag (day before långfredag) starts at 17:00
        elif (
            date == julafton(year)
            or date == midsommarafton(year)
            or date == nyarsafton(year)
            or date == nyarsafton(year - 1)
        ):
            start_time = "00:00"  # Full day since day before already starts at 17:00
        else:
            start_time = "00:00"
        rules.append(
            OnCallRule(
                code="OC_SPECIAL",
                label="Beredskap storhelg",
                specific_dates=[date.isoformat()],
                start_time=start_time,
                end_time="00:00",
                fixed_hourly_rate=192,
                priority=5,
                generated=True,
            )
        )

    # Add rules for first weekday morning (00:00-07:00) after each storhelg block
    # Christmas: first weekday after Dec 26
    christmas_first_weekday = first_weekday_after(datetime.date(year, 12, 26))
    if christmas_first_weekday not in storhelg_dates:
        rules.append(
            OnCallRule(
                code="OC_SPECIAL",
                label="Beredskap storhelg",
                specific_dates=[christmas_first_weekday.isoformat()],
                start_time="00:00",
                end_time="07:00",
                fixed_hourly_rate=192,
                priority=5,
                generated=True,
            )
        )

    # New Year: first weekday after Jan 1 (current year)
    newyear_first_weekday = first_weekday_after(nyarsdagen(year))
    if newyear_first_weekday not in storhelg_dates:
        rules.append(
            OnCallRule(
                code="OC_SPECIAL",
                label="Beredskap storhelg",
                specific_dates=[newyear_first_weekday.isoformat()],
                start_time="00:00",
                end_time="07:00",
                fixed_hourly_rate=192,
                priority=5,
                generated=True,
            )
        )

    # Easter: first weekday after Annandag påsk
    easter_first_weekday = first_weekday_after(annandagpask(year))
    if easter_first_weekday not in storhelg_dates:
        rules.append(
            OnCallRule(
                code="OC_SPECIAL",
                label="Beredskap storhelg",
                specific_dates=[easter_first_weekday.isoformat()],
                start_time="00:00",
                end_time="07:00",
                fixed_hourly_rate=192,
                priority=5,
                generated=True,
            )
        )

    # Midsummer: first weekday after Midsommardagen
    midsummer_day = midsommarafton(year) + datetime.timedelta(days=1)
    midsummer_first_weekday = first_weekday_after(midsummer_day)
    if midsummer_first_weekday not in storhelg_dates:
        rules.append(
            OnCallRule(
                code="OC_SPECIAL",
                label="Beredskap storhelg",
                specific_dates=[midsummer_first_weekday.isoformat()],
                start_time="00:00",
                end_time="07:00",
                fixed_hourly_rate=192,
                priority=5,
                generated=True,
            )
        )

    # Regular holidays exclude storhelg and Nationaldagen block
    regular_holidays = holidays - storhelg_dates - nationaldagen_block
    for date in sorted(regular_holidays):
        # Full day at holiday rate
        rules.append(
            OnCallRule(
                code="OC_HOLIDAY",
                label="Beredskap röd dag",
                specific_dates=[date.isoformat()],
                start_time="00:00",
                end_time="24:00",
                fixed_hourly_rate=112,
                priority=4,
                generated=True,
            )
        )

        # Check if holiday falls on Fri-Sun for extended block
        weekday = date.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun

        # Eve before holiday: 17:00-24:00 at holiday rate (112 kr)
        eve = date - datetime.timedelta(days=1)
        if eve not in holidays and eve not in storhelg_dates and eve not in nationaldagen_block:
            rules.append(
                OnCallRule(
                    code="OC_HOLIDAY",
                    label="Beredskap röd dag",
                    specific_dates=[eve.isoformat()],
                    start_time="17:00",
                    end_time="24:00",
                    fixed_hourly_rate=112,
                    priority=4,
                    generated=True,
                )
            )

        # If holiday falls on Fri-Sun, extend to first weekday morning (like storhelg)
        if weekday >= 4:  # Friday, Saturday, or Sunday
            # Extend to first weekday after this holiday
            first_weekday = first_weekday_after(date)
            current = date + datetime.timedelta(days=1)

            # Add full days until day before first weekday
            while current < first_weekday:
                if current not in holidays and current not in storhelg_dates and current not in nationaldagen_block:
                    rules.append(
                        OnCallRule(
                            code="OC_HOLIDAY",
                            label="Beredskap röd dag",
                            specific_dates=[current.isoformat()],
                            start_time="00:00",
                            end_time="24:00",
                            fixed_hourly_rate=112,
                            priority=4,
                            generated=True,
                        )
                    )
                current += datetime.timedelta(days=1)

            # Add first weekday morning (00:00-07:00)
            if (
                first_weekday not in holidays
                and first_weekday not in storhelg_dates
                and first_weekday not in nationaldagen_block
            ):
                rules.append(
                    OnCallRule(
                        code="OC_HOLIDAY",
                        label="Beredskap röd dag",
                        specific_dates=[first_weekday.isoformat()],
                        start_time="00:00",
                        end_time="07:00",
                        fixed_hourly_rate=112,
                        priority=4,
                        generated=True,
                    )
                )
        else:
            # Weekday holiday: only add morning after (00:00-07:00)
            day_after = date + datetime.timedelta(days=1)
            if day_after not in holidays and day_after not in storhelg_dates and day_after not in nationaldagen_block:
                rules.append(
                    OnCallRule(
                        code="OC_HOLIDAY",
                        label="Beredskap röd dag",
                        specific_dates=[day_after.isoformat()],
                        start_time="00:00",
                        end_time="07:00",
                        fixed_hourly_rate=112,
                        priority=4,
                        generated=True,
                    )
                )

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


def _subtract_covered_oncall(
    start: datetime.datetime,
    end: datetime.datetime,
    covered: list[tuple[datetime.datetime, datetime.datetime]],
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """
    Find time segments NOT covered by higher priority rules.

    This implements the REPLACEMENT logic: if a higher priority rule
    covers a time segment, lower priority rules don't apply there.

    Args:
        start: Start of interval to check
        end: End of interval to check
        covered: List of (start, end) tuples already claimed by higher priority

    Returns:
        List of (start, end) tuples representing uncovered time
    """
    if not covered:
        return [(start, end)]

    # Sort covered intervals by start time
    sorted_covered = sorted(covered, key=lambda x: x[0])

    uncovered = []
    cursor = start

    for cov_start, cov_end in sorted_covered:
        # Skip intervals that don't overlap
        if cov_end <= cursor or cov_start >= end:
            continue

        # Add gap before this covered interval
        if cov_start > cursor:
            uncovered.append((cursor, min(cov_start, end)))

        # Move cursor past this covered interval
        cursor = max(cursor, cov_end)

        if cursor >= end:
            break

    # Add remaining time after all covered intervals
    if cursor < end:
        uncovered.append((cursor, end))

    return uncovered


def calculate_oncall_pay(
    date: datetime.date,
    monthly_salary: int,
    oncall_rules: list[OnCallRule] | None = None,
    rate_overrides: dict[str, int | float] | None = None,
) -> dict:
    """
    Calculate on-call compensation for a 24-hour shift.

    On-call shifts run from 00:00 on the given date to 00:00 the next day.

    Args:
        date: Date when on-call shift starts (at 00:00)
        monthly_salary: Employee's monthly salary for rate calculation
        oncall_rules: Optional list of rules (if None, uses cached rules for year)

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

    # Track covered time and results
    covered: list[tuple[datetime.datetime, datetime.datetime]] = []
    breakdown: dict[str, dict] = {}
    segments: list[dict] = []

    # Process rules in priority order
    for rule in sorted_rules:
        # Get intervals where this rule applies
        intervals = _get_rule_intervals_for_shift(rule, shift_start, shift_end)

        for interval_start, interval_end in intervals:
            # Find uncovered portions of this interval
            uncovered = _subtract_covered_oncall(interval_start, interval_end, covered)

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
            uncovered = _subtract_covered_oncall(interval_start, interval_end, covered_intervals)

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


def calculate_oncall_pay_for_month(
    year: int,
    month: int,
    oncall_dates: list[datetime.date],
    monthly_salary: int,
) -> dict:
    """
    Calculate total on-call compensation for multiple dates in a month.

    Args:
        year: Year
        month: Month (1-12)
        oncall_dates: List of dates when person was on-call
        monthly_salary: Monthly salary for rate calculations

    Returns:
        Dict with total_pay, num_shifts, and per-date breakdown
    """
    oncall_rules = _cached_oncall_rules(year)

    results = []
    total_pay = 0.0

    for date in oncall_dates:
        if date.year == year and date.month == month:
            result = calculate_oncall_pay(date, monthly_salary, oncall_rules)
            results.append(result)
            total_pay += result["total_pay"]

    return {
        "year": year,
        "month": month,
        "total_pay": round(total_pay, 2),
        "num_shifts": len(results),
        "shifts": results,
    }


def clear_oncall_cache():
    """Clear cached on-call rules (call after rule changes)."""
    _cached_oncall_rules.cache_clear()
