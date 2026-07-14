# app/routes/schedule_all.py
"""
Team-wide schedule view routes - week, month, and year views for all persons.
"""

import calendar as _calendar
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user_optional
from app.core.constants import WEEKDAY_NAMES
from app.core.helpers import can_see_salary, render_template, strip_salary_data
from app.core.holidays import get_holiday_dates_for_year
from app.core.logging_config import get_logger
from app.core.oncall import _get_storhelg_dates_for_year
from app.core.rates import get_user_rates
from app.core.schedule import (
    build_substitute_month_summaries,
    build_week_data,
    generate_month_data,
    generate_year_data,
    get_all_user_wages,
    get_shift_types,
    rotation_start_date,
    summarize_month_for_person,
)
from app.core.schedule.period import mask_days_to_employment
from app.core.schedule.person_history import get_position_holder_segments, get_user_person_id, has_position_history
from app.core.schedule.summary import _calculate_tax
from app.core.utils import get_navigation_dates, get_safe_today, get_today
from app.core.validators import validate_date_params
from app.database.database import User, UserRole, WageType, get_db
from app.routes.shared import templates

logger = get_logger(__name__)

router = APIRouter(tags=["schedule_all"])


def _off_cell(cell: dict, name: str) -> dict:
    """Mask a person cell to OFF for a day outside a holder's segment.

    Mirrors the shape of a before-employment cell: identity keys are kept, the
    shift is cleared and the before_employment flag is set so the template
    renders it as a plain OFF day.
    """
    masked = dict(cell)
    masked["person_name"] = name
    masked["shift"] = None
    masked["before_employment"] = True
    return masked


def _build_person_rows(db: Session, days_in_week: list[dict], monday: date, sunday: date) -> list[dict]:
    """Build one week row per person holding a position during the week.

    A person holding a single position throughout the week (the common case,
    including an ordinary succession where a different person took over
    mid-week) yields exactly one row, masked to their own tenure as before.
    A person holding two or more DIFFERENT positions during the week (a
    position swap) is merged into ONE row: each day's cell is pulled from
    whichever position they actually held on that specific date. A position
    with no holder at all during the week is skipped entirely (no vacant
    placeholder row). Substitute entries (person_id outside 1-10) are
    appended unchanged.
    """
    from app.core.utils import get_today

    def _cell_for(day: dict, pid: int) -> dict | None:
        return next((p for p in day.get("persons", []) if p.get("person_id") == pid), None)

    legacy_rows: list[dict] = []
    segments_by_user: dict[int, list[dict]] = {}
    for pid in range(1, 11):
        segments = get_position_holder_segments(db, pid, monday, sunday)
        if not segments:
            if has_position_history(db, pid):
                continue  # Fully vacant for the whole week: no row.
            base_cells = [_cell_for(day, pid) for day in days_in_week]
            name = base_cells[0]["person_name"] if base_cells[0] else f"Person {pid}"
            legacy_rows.append(
                {
                    "person_id": pid,
                    "person_name": name,
                    "vacant": False,
                    "holder_user_id": pid,
                    "cells": base_cells,
                }
            )
            continue
        for seg in segments:
            segments_by_user.setdefault(seg["user_id"], []).append({**seg, "person_id": pid})

    real_today = get_today()
    merged_rows: list[dict] = []
    for user_id, segs in segments_by_user.items():
        segs.sort(key=lambda s: s["from_date"])
        positions_held = {s["person_id"] for s in segs}
        name = segs[-1]["name"]

        if len(positions_held) == 1:
            pid = segs[0]["person_id"]
            base_cells = [_cell_for(day, pid) for day in days_in_week]
            cells = []
            for day, cell in zip(days_in_week, base_cells, strict=True):
                if cell is None:
                    cells.append(None)
                elif any(s["from_date"] <= day["date"] <= s["to_date"] for s in segs):
                    cells.append(cell)
                else:
                    cells.append(_off_cell(cell, name))
        else:
            pid = get_user_person_id(db, user_id, on_date=real_today) or segs[-1]["person_id"]
            cells = []
            for day in days_in_week:
                seg_for_day = next((s for s in segs if s["from_date"] <= day["date"] <= s["to_date"]), None)
                cells.append(_cell_for(day, seg_for_day["person_id"]) if seg_for_day else None)

        merged_rows.append(
            {
                "person_id": pid,
                "person_name": name,
                "vacant": False,
                "holder_user_id": user_id,
                "cells": cells,
            }
        )

    person_rows = sorted(legacy_rows + merged_rows, key=lambda r: r["person_id"])

    if days_in_week:
        for entry in days_in_week[0].get("persons", []):
            sub_pid = entry.get("person_id")
            if isinstance(sub_pid, int) and 1 <= sub_pid <= 10:
                continue
            cells = [_cell_for(day, sub_pid) for day in days_in_week]
            person_rows.append(
                {
                    "person_id": sub_pid,
                    "person_name": entry.get("person_name", ""),
                    "vacant": False,
                    "is_substitute": True,
                    "substitute_id": entry.get("substitute_id"),
                    "cells": cells,
                }
            )

    return person_rows


def _count_week_based_parental_days(days: list[dict]) -> int:
    """Count week-based parental-leave days flagged on a segment's own `days` list.

    This is distinct from day-level PARENTAL absence rows (see the module docstring
    on _merge_month_summaries): mask_days_to_employment clears this flag on days
    outside a segment's own range, so counting it per segment and summing across
    segments is correct.
    """
    return sum(1 for d in days if d.get("parental_leave"))


def _merge_month_summaries(summaries: list[dict]) -> dict:
    """Combine date-disjoint month summaries for the same person into one.

    Each input summary was built from year_days masked to one segment's own
    date range via mask_days_to_employment (every day outside that segment is
    already zeroed to OFF with no pay). Segments for the same person during
    one month never overlap in time (PersonHistory allows only one open
    record per position), so merging the `days` lists is safe: take the first
    summary's `days` list and overlay any non-OFF day from later summaries.

    The aggregate fields, however, fall into three groups that must be merged
    differently:

    1. Day-derived fields (`_SUM_FIELDS` / `_SUM_DICT_FIELDS`): accumulated in
       summary.py's _process_day_for_summary from each segment's own masked
       `days` list, so every segment's contribution is genuinely its own share
       of the month. Safe to sum across segments.

    2. Whole-month absence-derived fields (`_ABSENCE_ONLY_FIELDS` /
       `_ABSENCE_ONLY_DICT_FIELDS`): summary.py's summarize_month_for_person
       calls get_absence_deductions_for_month(session, uid_for_wages, year,
       month, ...) (wages.py), which queries Absence rows by user_id for the
       ENTIRE calendar month with no date-range/segment scoping at all. Since
       every segment of a swap belongs to the SAME user_id, each segment's
       summary independently carries the identical whole-month absence
       figures. Summing them across N segments would multiply them by N;
       instead take them from exactly one segment (summaries[0]) since they
       are already identical across all segments.

    3. `parental_days`: a MIX of the two. summary.py adds a day-derived
       "week_parental_days" count (from the day-level `parental_leave` flag,
       correctly zeroed outside each segment by mask_days_to_employment) on
       top of the whole-month absence query's PARENTAL-type day count. Summed
       naively it double-counts the absence component; taken from one segment
       it drops the other segments' week-based days. It is reconstructed here
       by subtracting summaries[0]'s own week-based count back out (to
       recover the absence-only component) and adding the week-based counts
       from every segment.

    `brutto_pay`/`netto_pay` have their own dedicated merge in
    _merge_brutto_netto (a similar but distinct flat-base-vs-variable-pay
    split); this function does not touch that logic.
    """
    merged = dict(summaries[0])

    merged_days = list(summaries[0]["days"])
    for other in summaries[1:]:
        for i, other_day in enumerate(other["days"]):
            if other_day.get("shift") and other_day["shift"].code != "OFF":
                merged_days[i] = other_day
    merged["days"] = merged_days

    # Group 1: genuinely day-derived, safe to sum across segments.
    sum_fields = [
        "total_hours",
        "num_shifts",
        "oncall_pay",
        "oncall_hours",
        "ot_pay",
        "ot_hours",
        "vacation_days",
    ]
    for field in sum_fields:
        merged[field] = sum(s.get(field) or 0 for s in summaries)

    # Group 2: sourced from the whole-month absence query, identical across
    # every segment of the same user/month. Take from one segment only.
    absence_only_fields = [
        "absence_deduction",
        "absence_hours",
        "sick_days",
        "sick_hours",
        "sick_ob_pay",
        "sick_total_ob",
        "sick_ob_lost",
        "vab_days",
        "vab_hours",
        "leave_days",
        "leave_hours",
        "off_days",
        "off_hours",
        "parental_hours",
    ]
    for field in absence_only_fields:
        merged[field] = summaries[0].get(field) or 0

    # Group 1 dict fields: per-OB-code breakdowns derived from each segment's
    # own masked shifts. Safe to sum per key.
    sum_dict_fields = ["ob_hours", "ob_pay"]
    for field in sum_dict_fields:
        combined: dict = {}
        for s in summaries:
            for code, value in (s.get(field) or {}).items():
                combined[code] = combined.get(code, 0.0) + value
        merged[field] = combined

    # Group 2 dict fields: also sourced from the whole-month absence query.
    # Take from one segment, do not sum.
    absence_only_dict_fields = ["sick_ob_pay_by_code", "sick_ob_hours_by_code"]
    for field in absence_only_dict_fields:
        merged[field] = dict(summaries[0].get(field) or {})

    # Group 3: parental_days mixes a whole-month absence component (constant
    # across segments) with a day-derived week-based component (varies per
    # segment). Recover the absence-only component from summaries[0] and add
    # every segment's own week-based count.
    first_week_based = _count_week_based_parental_days(summaries[0].get("days") or [])
    absence_only_parental_days = (summaries[0].get("parental_days") or 0) - first_week_based
    total_week_based = sum(_count_week_based_parental_days(s.get("days") or []) for s in summaries)
    merged["parental_days"] = absence_only_parental_days + total_week_based

    merged["absence_details"] = [d for s in summaries for d in s.get("absence_details", [])]

    merged["brutto_pay"], merged["netto_pay"] = _merge_brutto_netto(summaries)

    return merged


def _merge_brutto_netto(summaries: list[dict]) -> tuple[float, float]:
    """Combine brutto/netto pay across segments without double-counting whole-month components.

    A monthly-wage segment's brutto_pay (see summary.py's summarize_month_for_person)
    is built as:

        base_salary + day_derived_variable_pay - absence_deduction + sick_ob_pay

    where day_derived_variable_pay is OB/oncall/OT pay accumulated from the
    segment's own masked `days` (genuinely this segment's share, safe to sum),
    but absence_deduction and sick_ob_pay both come from
    get_absence_deductions_for_month queried unscoped for the ENTIRE month by
    user_id (wages.py) - identical across every segment of the same swap. When
    a swap participant's month is split into per-position segments, each
    segment independently resolves the SAME base_salary AND the SAME
    absence_deduction/sick_ob_pay. Naively summing brutto_pay across segments
    would therefore count the flat base, the absence deduction, and the
    sick-OB addition once per segment instead of once for the whole month.

    The merged gross is reconstructed from scratch: recover each segment's own
    day-derived variable pay by removing the shared base and undoing the
    shared absence adjustments from its brutto_pay, sum those variable parts,
    then add back exactly one base_salary, one absence_deduction subtraction,
    and one sick_ob_pay addition. absence_deduction/sick_ob_pay are already
    correctly single (not summed) per the commit bb143aa fix to
    _merge_month_summaries, so they can be read directly off summaries[0].

    Hourly-wage users don't have this problem: summarize_month_for_person fully
    replaces their brutto_pay with a worked-hours-derived figure
    (_hourly_corrected_gross) that carries no flat base component at all and is
    already zero for the masked-out days of every segment but their own, so
    summing it across segments is correct as-is.
    """
    if len(summaries) == 1:
        return summaries[0]["brutto_pay"], summaries[0]["netto_pay"]

    if summaries[0].get("wage_type") == WageType.HOURLY:
        merged_brutto = sum(s.get("brutto_pay") or 0 for s in summaries)
    else:
        base_salary = summaries[0].get("base_salary") or 0
        absence_deduction = summaries[0].get("absence_deduction") or 0
        sick_ob_pay = summaries[0].get("sick_ob_pay") or 0
        variable_pay_total = sum(
            (s.get("brutto_pay") or 0) - base_salary + absence_deduction - sick_ob_pay for s in summaries
        )
        merged_brutto = base_salary + variable_pay_total - absence_deduction + sick_ob_pay

    tax_table = summaries[0].get("tax_table")
    payment_year = summaries[0].get("year")
    merged_netto = merged_brutto - _calculate_tax(merged_brutto, tax_table, payment_year=payment_year)
    return merged_brutto, merged_netto


def _resolve_month_rates_map(
    db: Session, position_id: int, wage_user_id: int, effective_date: date
) -> dict[int, dict] | None:
    """Resolve a holder's effective rates (RateHistory) as of effective_date,
    keyed by the position id used for that generate_month_data call.

    Without this, per-day OT pay silently falls back to the generic
    monthly-wage/OT_RATE_DIVISOR formula instead of a holder's actual stored
    OT rate override - mirroring the fix already applied to the personal
    month view's build_calendar_grid_for_month (commit ea9ec28). Rates belong
    to the real user (RateHistory.user_id), not the rotation position, so
    this must be resolved per holder: a position with two different holders
    during the same month needs two separate calls, one per holder, each
    keyed by that holder's own segment's position_id.

    effective_date must be the holder's OWN segment start (clamped into the
    viewed month), not unconditionally the first of the month: a holder whose
    tenure begins mid-month has no RateHistory row covering the month's start
    at all, which would otherwise resolve nothing and silently fall back to
    the generic formula for a holder who does have a stored rate.
    """
    rate_user = db.query(User).filter(User.id == wage_user_id).first()
    if rate_user is None:
        return None
    rates = get_user_rates(rate_user, session=db, effective_date=effective_date)
    if not rates:
        return None
    return {position_id: rates}


@router.get("/week", response_class=HTMLResponse, name="week_all")
async def show_week_all(
    request: Request,
    year: int = None,
    week: int = None,
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Week view for all persons."""
    safe_today = get_safe_today(rotation_start_date)

    iso_year, iso_week, _ = safe_today.isocalendar()

    year = year or iso_year
    week = week or iso_week

    days_in_week = build_week_data(year, week, session=db)

    monday = date.fromisocalendar(year, week, 1)
    sunday = monday + timedelta(days=6)
    nav = get_navigation_dates("week", monday)

    person_rows = _build_person_rows(db, days_in_week, monday, sunday)

    real_today = get_today()

    storhelg_dates = _get_storhelg_dates_for_year(year)
    holiday_dates = get_holiday_dates_for_year(year)

    return render_template(
        templates,
        "week_all.html",
        request,
        {
            "year": year,
            "week": week,
            "days": days_in_week,
            "person_rows": person_rows,
            "today": real_today,
            "storhelg_dates": storhelg_dates,
            "holiday_dates": holiday_dates,
            **nav,
        },
        user=current_user,
    )


@router.get("/month", response_class=HTMLResponse, name="month_all")
async def show_month_all(
    request: Request,
    year: int = None,
    month: int = None,
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Month view for all persons."""
    start_time = datetime.now()

    safe_today = get_safe_today(rotation_start_date)

    year = year or safe_today.year
    month = month or safe_today.month

    validate_date_params(year, month, None)

    # Pre-load wages once to avoid N+1 queries (10 persons × 1 query each)
    user_wages = get_all_user_wages(db)

    # Only fetch tax tables if user is admin (needed for salary calculations)
    is_admin = current_user is not None and current_user.role == UserRole.ADMIN

    month_start = date(year, month, 1)
    month_end = date(year, month, _calendar.monthrange(year, month)[1])

    # First pass: scan every position and collect its holder segments, keyed
    # by user_id ACROSS ALL positions (not just the position currently being
    # scanned). A position with no history at all resolves to a legacy single
    # column right away (there is no PersonHistory user_id to group it by). A
    # fully vacant position (has history but no segment overlaps this month)
    # is skipped entirely: no placeholder column.
    persons = []
    segments_by_user: dict[int, list[dict]] = {}
    for pid in range(1, 11):
        segments = get_position_holder_segments(db, pid, month_start, month_end)

        if not segments:
            if has_position_history(db, pid):
                continue  # Fully vacant for the whole month: no column.
            # Legacy position with no PersonHistory at all: single column,
            # current behavior (no masking, wage resolved by position id).
            # Generate MONTH data ONCE per person (30-31 days instead of 365 days - 12x faster!)
            rates_map = _resolve_month_rates_map(db, pid, pid, month_start)
            person_month_days = generate_month_data(
                year, month, pid, session=db, user_wages=user_wages, user_rates_map=rates_map
            )
            summary = summarize_month_for_person(
                year,
                month,
                pid,
                session=db,
                user_wages=user_wages,
                year_days=person_month_days,
                fetch_tax_table=is_admin,
                payment_year=year,
            )
            summary["holder_user_id"] = pid
            if not can_see_salary(current_user, pid):
                summary = strip_salary_data(summary)
            persons.append(summary)
            continue

        for seg in segments:
            segments_by_user.setdefault(seg["user_id"], []).append({**seg, "person_id": pid})

    # Second pass: one column per user, built from their COMPLETE segment set
    # across every position. A user holding a single position throughout the
    # month (the common case, including an ordinary succession where a
    # different person took over mid-month) yields one column, masked to
    # their own tenure. A user holding two or more DIFFERENT positions during
    # the month (a position swap) is merged into ONE column: each day's
    # figures are pulled from whichever position they actually held on that
    # specific date, and the aggregate totals are summed across positions.
    for user_id, segs in segments_by_user.items():
        segs.sort(key=lambda s: s["from_date"])

        per_segment_summaries = []
        for seg in segs:
            # Rates belong to the real user (RateHistory), not the rotation
            # position, and each segment may be a DIFFERENT holder of the
            # same position across the month (an ordinary succession) or the
            # SAME user across different positions (a swap). Either way,
            # resolve and price THIS segment with its own holder's rates,
            # not a rate resolved once for the whole position's column.
            rates_map = _resolve_month_rates_map(db, seg["person_id"], user_id, max(seg["from_date"], month_start))
            segment_month_days = generate_month_data(
                year, month, seg["person_id"], session=db, user_wages=user_wages, user_rates_map=rates_map
            )
            masked_days = mask_days_to_employment(segment_month_days, seg["from_date"], seg["to_date"])
            s = summarize_month_for_person(
                year,
                month,
                seg["person_id"],
                session=db,
                user_wages=user_wages,
                year_days=masked_days,
                fetch_tax_table=is_admin,
                payment_year=year,
                wage_user_id=user_id,
            )
            s["person_name"] = seg["name"]
            per_segment_summaries.append(s)

        summary = (
            per_segment_summaries[0]
            if len(per_segment_summaries) == 1
            else _merge_month_summaries(per_segment_summaries)
        )
        summary["person_name"] = segs[-1]["name"]
        summary["holder_user_id"] = user_id
        if len({s["person_id"] for s in segs}) > 1:
            # Swap participant: display their CURRENT position, matching the
            # year view's approach (get_user_person_id) instead of the
            # earliest/pre-swap position the first segment happens to carry.
            summary["person_id"] = get_user_person_id(db, user_id, on_date=get_today()) or summary["person_id"]
        viewer_is_owner = current_user is not None and current_user.id == user_id
        if not (is_admin or viewer_is_owner):
            summary = strip_salary_data(summary)
        persons.append(summary)

    # Restore strict position-id ascending order: the two-pass split above
    # resolves legacy/vacant columns eagerly (first pass) and per-user
    # columns afterwards (second pass), which would otherwise group all
    # legacy columns before any history-tracked column regardless of position
    # number.
    persons.sort(key=lambda p: p["person_id"])

    # Append substitutes (schedule only, no salary) after the regular positions
    persons.extend(build_substitute_month_summaries(year, month, db))

    show_salary = current_user is not None and current_user.role == UserRole.ADMIN

    # Calculate and log load time
    end_time = datetime.now()
    load_time = (end_time - start_time).total_seconds()
    logger.info(
        f"Route /month (all persons) (year={year}, month={month}) loaded in {load_time:.3f}s",
        extra={"duration_ms": load_time * 1000, "path": "/month", "user_id": current_user.id if current_user else None},
    )

    storhelg_dates = _get_storhelg_dates_for_year(year)
    holiday_dates = get_holiday_dates_for_year(year)

    return render_template(
        templates,
        "month_all.html",
        request,
        {
            "year": year,
            "month": month,
            "persons": persons,
            "show_salary": show_salary,
            "storhelg_dates": storhelg_dates,
            "holiday_dates": holiday_dates,
            "today": get_today(),
        },
        user=current_user,
    )


@router.get("/year", response_class=HTMLResponse, name="year_all")
async def show_year_all(
    request: Request,
    year: int = None,
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
    simulated_date: str = None,
):
    """Year view for all persons."""
    start_time = datetime.now()

    # Testing aid: ?simulated_date=YYYY-MM-DD views the page as if today were
    # that date (default year selection and past/future column hiding).
    # Invalid values fall back to the real date instead of erroring.
    sim_today = None
    if simulated_date:
        try:
            sim_today = date.fromisoformat(simulated_date.strip())
        except ValueError:
            sim_today = None

    safe_today = sim_today or get_safe_today(rotation_start_date)
    year = year or safe_today.year

    # Pre-load wages once to avoid N+1 queries (10 persons × 12 months = 120 queries → 1 query)
    user_wages = get_all_user_wages(db)

    days_in_year = generate_year_data(year, session=db, user_wages=user_wages)

    # Skip calculating totals on initial load - will be lazy-loaded via AJAX
    # This makes initial page load much faster (~0.5s instead of 1-3s)
    person_ob_totals = None

    # Build the column list with a two-pass restructure, matching the pattern
    # established in _build_person_rows (week view) and show_month_all (month
    # view). First pass: scan every position and collect its holder segments,
    # keyed by user_id ACROSS ALL positions (not just the position currently
    # being scanned). A position with no history at all resolves to a legacy
    # single column right away. A fully vacant position (has history but no
    # segment overlaps this year) is skipped entirely: no placeholder column.
    from app.core.schedule.person_history import get_current_person_for_position

    real_today = sim_today or get_today()
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    person_headers = []
    segments_by_user: dict[int, list[dict]] = {}
    for pid in range(1, 11):
        segments = get_position_holder_segments(db, pid, year_start, year_end)
        # Merge consecutive segments held by the same user so a single
        # employment split across adjacent history records stays one column and
        # its col_key (person_id-user_id) remains unique.
        merged: list[dict] = []
        for seg in segments:
            if merged and merged[-1]["user_id"] == seg["user_id"]:
                merged[-1]["to_date"] = seg["to_date"]
            else:
                merged.append(dict(seg))

        if not merged:
            if has_position_history(db, pid):
                continue  # Fully vacant for the whole year: no column.
            # Legacy position without history: link target is the position itself.
            cp = get_current_person_for_position(db, pid)
            person_headers.append(
                {
                    "person_id": pid,
                    "user_id": pid,
                    "name": cp["name"] if cp else f"Person {pid}",
                    "vacant": False,
                    "col_key": f"{pid}-{pid}",
                    "from_date": year_start,
                    "to_date": year_end,
                    "past": False,
                    "future": False,
                }
            )
            continue

        for seg in merged:
            segments_by_user.setdefault(seg["user_id"], []).append({**seg, "person_id": pid})

    # Second pass: one column per user, built from their COMPLETE segment set
    # across every position. A user holding a single position throughout the
    # year (the common case, including an ordinary succession where a
    # different person took over mid-year) yields one column, unchanged in
    # shape from before: a departed holder whose last working day is already
    # past is flagged so the template can hide their column by default, and a
    # holder whose tenure begins after today is future-dated (hidden until its
    # start passes). A user holding two or more DIFFERENT positions during the
    # year (a position swap) is merged into ONE column: each day's cell is
    # resolved via position_by_date, a map of ISO date string -> person_id
    # that only the template's merged-column branch consults. A swap
    # participant's column is never flagged past or future as a whole -
    # departures/future hires stay on separate user_ids and are unaffected.
    for user_id, segs in segments_by_user.items():
        segs.sort(key=lambda s: s["effective_from"])
        positions_held = {s["person_id"] for s in segs}

        if len(positions_held) == 1:
            seg = segs[0]
            to_date = seg["to_date"]
            from_date = seg["from_date"]
            past = to_date is not None and to_date < real_today
            # Use the raw employment start, not the window-clamped from_date,
            # so an ongoing holder viewed in a later year is not mistaken for
            # a future hire.
            future = seg["effective_from"] > real_today
            person_headers.append(
                {
                    "person_id": seg["person_id"],
                    "user_id": user_id,
                    "name": seg["name"],
                    "vacant": False,
                    "col_key": f"{seg['person_id']}-{user_id}",
                    "from_date": from_date,
                    "to_date": to_date,
                    "past": past,
                    "future": future,
                }
            )
        else:
            current_pid = get_user_person_id(db, user_id, on_date=real_today) or segs[-1]["person_id"]
            position_by_date: dict[str, int] = {}
            d = min(s["from_date"] for s in segs)
            end = max(s["to_date"] for s in segs)
            while d <= end:
                seg_for_day = next((s for s in segs if s["from_date"] <= d <= s["to_date"]), None)
                if seg_for_day:
                    position_by_date[d.isoformat()] = seg_for_day["person_id"]
                d += timedelta(days=1)
            person_headers.append(
                {
                    "person_id": current_pid,
                    "user_id": user_id,
                    "name": segs[-1]["name"],
                    "vacant": False,
                    "col_key": f"user-{user_id}",
                    "from_date": min(s["from_date"] for s in segs),
                    "to_date": max(s["to_date"] for s in segs),
                    "past": False,
                    "future": False,
                    "position_by_date": position_by_date,
                }
            )

    # Restore strict position-id ascending order: the two-pass split above
    # resolves legacy/vacant columns eagerly (first pass) and per-user columns
    # afterwards (second pass), which would otherwise group all legacy columns
    # before any history-tracked column regardless of position number.
    person_headers.sort(key=lambda h: h["person_id"])

    show_salary = current_user is not None and current_user.role == UserRole.ADMIN

    # Calculate and log load time
    end_time = datetime.now()
    load_time = (end_time - start_time).total_seconds()

    logger.info(
        f"Route /year (all persons) loaded in {load_time:.3f}s",
        extra={"duration_ms": load_time * 1000, "path": "/year", "user_id": current_user.id if current_user else None},
    )

    storhelg_dates = _get_storhelg_dates_for_year(year)
    holiday_dates = get_holiday_dates_for_year(year)

    return render_template(
        templates,
        "year_all.html",
        request,
        {
            "year": year,
            "days": days_in_year,
            "person_ob_totals": person_ob_totals,
            "person_headers": person_headers,
            "show_salary": show_salary,
            "storhelg_dates": storhelg_dates,
            "holiday_dates": holiday_dates,
            "today": real_today,
        },
        user=current_user,
    )


@router.get("/handover", response_class=HTMLResponse, name="handover")
async def show_handover(
    request: Request,
    date: str = None,
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Daily handover report grouped by shift type."""
    today = get_today()

    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            target_date = today
    else:
        target_date = today

    iso_year, iso_week, _ = target_date.isocalendar()
    days_in_week = build_week_data(iso_year, iso_week, session=db)

    day_data = next((d for d in days_in_week if d["date"] == target_date), None)

    shift_groups = [
        {"code": "N1", "label": "Morgonpass", "persons": []},
        {"code": "N2", "label": "Kvällspass", "persons": []},
        {"code": "N3", "label": "Nattpass", "persons": []},
        {"code": "OC", "label": "Beredskap", "persons": []},
    ]

    if day_data and "persons" in day_data:
        code_to_group = {g["code"]: g for g in shift_groups}
        end_time_to_code = {
            s.end_time: s.code for s in get_shift_types() if s.end_time and s.code in ("N1", "N2", "N3")
        }
        for person in day_data["persons"]:
            shift = person.get("shift")
            if not shift:
                continue
            if shift.code in code_to_group:
                code_to_group[shift.code]["persons"].append(person["person_name"])
            elif shift.code == "OT":
                end_dt = person.get("end")
                matched_code = end_time_to_code.get(end_dt.strftime("%H:%M")) if end_dt else None
                name = f"{person['person_name']} (ÖT)"
                if matched_code:
                    code_to_group[matched_code]["persons"].append(name)
                else:
                    code_to_group["N1"]["persons"].append(name)

    return render_template(
        templates,
        "handover.html",
        request,
        {
            "date": target_date,
            "weekday_name": WEEKDAY_NAMES[target_date.weekday()],
            "shift_groups": shift_groups,
            "prev_date": target_date - timedelta(days=1),
            "next_date": target_date + timedelta(days=1),
            "today": today,
        },
        user=current_user,
    )
