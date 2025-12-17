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

from calendar import week
import datetime
from functools import lru_cache
from typing import List, Dict, Set, Tuple


from .models import OnCallRule
from .holidays import (
    nyarsdagen,
    trettondagen,
    langfredagen,
    easter_sunday,
    annandagpask,
    forsta_maj,
    kristi_himmelsfardsdag,
    nationaldagen,
    midsommarafton,
    pingstafton,
    alla_helgons_dag,
    julafton,
    nyarsafton,
    skartorsdagen,
    first_weekday_after,
)
from .storage import load_oncall_rules

# Load base rules from JSON config
oncall_rules_base = load_oncall_rules()


def _get_holidays_for_year(year: int) -> Set[datetime.date]:
    """
    Return set of all Swedish public holidays for the year.
    
    Includes both fixed holidays (Jan 1, May 1, Jun 6, Dec 24-26) and
    moveable holidays (Easter-related, Midsummer, All Saints).
    """
    holidays = set()
    
    # Fixed holidays
    holidays.add(nyarsdagen(year))           # 1 jan
    holidays.add(trettondagen(year))         # 6 jan
    holidays.add(forsta_maj(year))           # 1 maj
    holidays.add(nationaldagen(year))        # 6 jun
    holidays.add(julafton(year))             # 24 dec
    holidays.add(datetime.date(year, 12, 25))  # Juldagen
    holidays.add(datetime.date(year, 12, 26))  # Annandag jul
    holidays.add(nyarsafton(year))           # 31 dec
    
    # Easter-related (moveable)
    holidays.add(langfredagen(year))         # Långfredag
    holidays.add(easter_sunday(year))        # Påskdagen
    holidays.add(annandagpask(year))         # Annandag påsk
    holidays.add(skartorsdagen(year))        # Skärtorsdag (afton)
    
    # Ascension Day
    holidays.add(kristi_himmelsfardsdag(year))
    
    # Pentecost
    holidays.add(pingstafton(year))          # Pingstafton
    pentecost = easter_sunday(year) + datetime.timedelta(days=49)
    holidays.add(pentecost)                  # Pingstdagen
    
    # Midsummer (Friday-Saturday between Jun 19-25)
    holidays.add(midsommarafton(year))
    midsummer_day = midsommarafton(year) + datetime.timedelta(days=1)
    holidays.add(midsummer_day)
    
    # All Saints Day (Saturday between Oct 31 - Nov 6)
    holidays.add(alla_helgons_dag(year))
    
    return holidays


def _get_storhelg_dates_for_year(year: int) -> Set[datetime.date]:
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
    
    # Pentecost block: Pingstafton → first weekday after Pingstdagen
    pingst_start = pingstafton(year)
    pingst_end = first_weekday_after(easter_sunday(year) + datetime.timedelta(days=49))
    d = pingst_start
    while d < pingst_end:
        storhelg.add(d)
        d += datetime.timedelta(days=1)
    
    return storhelg

def build_oncall_rules_for_year(year: int) -> List[OnCallRule]:
    """
    Build complete list of on-call rules for a specific year.
    
    Combines:
    1. Static rules from oncall_rules.json (weekday-based)
    2. Dynamically generated rules for specific holiday dates
    
    Returns list sorted by priority (used for replacement logic).
    """
    rules: List[OnCallRule] = []

    for rule in oncall_rules_base:
        rules.append(rule)

    holidays = _get_holidays_for_year(year)
    storhelg_dates = _get_storhelg_dates_for_year(year)

    for date in sorted(storhelg_dates):
        if date == skartorsdagen(year) or date == nyarsafton(year) or date == nyarsafton(year - 1):
            start_time = "18:00"
        elif date == julafton(year) or date == midsommarafton(year) or date == pingstafton(year):
            start_time = "07:00"
        else:
            start_time = "00:00"
        rules.append(OnCallRule(
            code="OC_SPECIAL",
            label="Oncall major holiday",
            specific_dates=[date.isoformat()],
            start_time=start_time,
            end_time="00:00",
            rate=250,
            priority=6,
            generated=True
        ))

    regular_holidays = holidays - storhelg_dates
    for date in sorted(regular_holidays):
        rules.append(OnCallRule(
            code="OC_HOLIDAY",
            label="Oncall holiday",
            specific_dates=[date.isoformat()],
            start_time="07:00",
            end_time="24:00",
            rate=500,
            priority=5,
            generated=True,
        ))

        eve = date - datetime.timedelta(days=1)
        if eve not in holidays and eve not in storhelg_dates:
            rules.append(OnCallRule(
                code="OC_HOLIDAY_EVE",
                label="Oncall holiday eve",
                specific_dates=[eve.isoformat()],
                start_time="18:00",
                end_time="24:00",
                rate=700,
                priority=4,
                generated=True,
            ))

    return rules


@lru_cache(maxsize=10)
def _cached_oncall_rules(year: int) -> List[OnCallRule]:
    """Cached version of build_oncall_rules_for_year."""
    return build_oncall_rules_for_year(year)

def _select_oncall_rules_for_date(
    dt: datetime.date,
    oncall_rules: List[OnCallRule],
) -> List[OnCallRule]:
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

def _parse_time(time_str: str) -> Tuple[int, int]:
    """Parse 'HH:MM' string to (hour, minute) tuple. Handles '24:00'."""
    if time_str == "24:00":
        return (24, 0)
    h, m = map(int, time_str.split(":"))
    return (h, m)

def _get_rule_intervals_for_shift(
    rule: OnCallRule,
    shift_start: datetime.datetime,
    shift_end: datetime.datetime,
) -> List[Tuple[datetime.datetime, datetime.datetime]]:
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
    ends_at_midnight = (rule_end_h == 24)
    if ends_at_midnight:
        rule_end_h = 0
    
    # Determine if rule spans midnight
    spans_midnight = rule.spans_to_next_day or (
        datetime.time(rule_end_h, rule_end_m) <= datetime.time(rule_start_h, rule_start_m)
        and not ends_at_midnight
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
            interval_start = datetime.datetime.combine(
                current_date,
                datetime.time(rule_start_h, rule_start_m)
            )
            
            if ends_at_midnight:
                interval_end = datetime.datetime.combine(
                    current_date + datetime.timedelta(days=1),
                    datetime.time(0, 0)
                )
            elif spans_midnight:
                interval_end = datetime.datetime.combine(
                    current_date + datetime.timedelta(days=1),
                    datetime.time(rule_end_h, rule_end_m)
                )
            else:
                interval_end = datetime.datetime.combine(
                    current_date,
                    datetime.time(rule_end_h, rule_end_m)
                )
            
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
    covered: List[Tuple[datetime.datetime, datetime.datetime]],
) -> List[Tuple[datetime.datetime, datetime.datetime]]:
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
    oncall_rules: List[OnCallRule] | None = None,
) -> Dict:
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
    shift_end = datetime.datetime.combine(
        date + datetime.timedelta(days=1),
        datetime.time(0, 0)
    )
    
    # Get rules if not provided
    if oncall_rules is None:
        oncall_rules = _cached_oncall_rules(date.year)
    
    # Sort rules by priority (highest first)
    sorted_rules = sorted(oncall_rules, key=lambda r: r.priority, reverse=True)
    
    # Track covered time and results
    covered: List[Tuple[datetime.datetime, datetime.datetime]] = []
    breakdown: Dict[str, Dict] = {}
    segments: List[Dict] = []
    
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
                    # Calculate pay for this segment
                    # On-call pay: (monthly_salary / rate) gives hourly rate
                    # For any number of hours: (monthly_salary / rate) * hours
                    segment_pay = (monthly_salary / rule.rate) * hours
                    
                    # Track in breakdown
                    if rule.code not in breakdown:
                        breakdown[rule.code] = {
                            "hours": 0.0,
                            "rate": rule.rate,
                            "pay": 0.0,
                            "label": rule.label,
                        }
                    breakdown[rule.code]["hours"] += hours
                    breakdown[rule.code]["pay"] += segment_pay
                    
                    # Track segment
                    segments.append({
                        "start": unc_start,
                        "end": unc_end,
                        "hours": hours,
                        "code": rule.code,
                        "rate": rule.rate,
                        "pay": segment_pay,
                    })
                    
                    # Mark as covered
                    covered.append((unc_start, unc_end))
    
    # Calculate totals
    total_pay = sum(b["pay"] for b in breakdown.values())
    total_hours = sum(b["hours"] for b in breakdown.values())
    
    # Calculate effective rate (weighted average)
    if total_hours > 0:
        weighted_rate = sum(
            b["rate"] * b["hours"] for b in breakdown.values()
        ) / total_hours
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
    oncall_rules: List[OnCallRule]
) -> Dict:
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
        return {
            "total_pay": 0.0,
            "breakdown": {},
            "total_hours": 0.0,
            "effective_rate": 0
        }

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
                segment_pay = (monthly_salary / rule.rate) * segment_hours

                # Add to breakdown
                if rule.code not in breakdown:
                    breakdown[rule.code] = {
                        "hours": 0.0,
                        "rate": rule.rate,
                        "pay": 0.0,
                        "label": rule.label
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
        "effective_rate": effective_rate
    }


def calculate_oncall_pay_for_month(
    year: int,
    month: int,
    oncall_dates: List[datetime.date],
    monthly_salary: int,
) -> Dict:
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