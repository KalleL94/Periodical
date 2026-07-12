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
from app.core.utils import get_navigation_dates, get_safe_today, get_today
from app.core.validators import validate_date_params
from app.database.database import User, UserRole, get_db
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

    persons = []
    for pid in range(1, 11):
        # Generate MONTH data ONCE per person (30-31 days instead of 365 days - 12x faster!)
        person_month_days = generate_month_data(year, month, pid, session=db, user_wages=user_wages)
        segments = get_position_holder_segments(db, pid, month_start, month_end)

        if not segments and has_position_history(db, pid):
            # Position vacated entirely: skip it (no placeholder column).
            continue

        if len(segments) <= 1:
            # Zero (legacy) or one holder: single column, current behavior
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
            if segments:
                summary["person_name"] = segments[0]["name"]
            # Personal-view link target: the single holder's user id, or the
            # rotation position itself for legacy positions without history.
            summary["holder_user_id"] = segments[0]["user_id"] if segments else pid
            if not can_see_salary(current_user, pid):
                summary = strip_salary_data(summary)
            persons.append(summary)
            continue

        # Mid-month change: one column per holder, days masked to their tenure
        for seg in segments:
            masked_days = mask_days_to_employment(person_month_days, seg["from_date"], seg["to_date"])
            summary = summarize_month_for_person(
                year,
                month,
                pid,
                session=db,
                user_wages=user_wages,
                year_days=masked_days,
                fetch_tax_table=is_admin,
                payment_year=year,
                wage_user_id=seg["user_id"],
            )
            summary["person_name"] = seg["name"]
            summary["holder_user_id"] = seg["user_id"]
            viewer_is_owner = current_user is not None and current_user.id == seg["user_id"]
            if not (is_admin or viewer_is_owner):
                summary = strip_salary_data(summary)
            persons.append(summary)

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

    # Build the column list: one column per holder segment of the displayed
    # year (like the month view), rather than a single joined-header column per
    # position. A position that changed hands mid-year yields one column per
    # holder in chronological order, each linking to their own personal year
    # view. A departed holder whose last working day is already past is flagged
    # so the template can hide their column by default. A position with only
    # closed history and no overlap in the year is a single vacant column.
    # Positions without any history keep one legacy column linked to the
    # rotation position itself.
    from app.core.schedule.person_history import get_current_person_for_position

    real_today = sim_today or get_today()
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    person_headers = []
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

        if merged:
            for seg in merged:
                to_date = seg["to_date"]
                from_date = seg["from_date"]
                past = to_date is not None and to_date < real_today
                # A holder whose tenure begins after today is future-dated: its
                # column stays hidden (like a past one) until its start passes.
                # Use the raw employment start, not the window-clamped from_date,
                # so an ongoing holder viewed in a later year is not mistaken for
                # a future hire.
                future = seg["effective_from"] > real_today
                person_headers.append(
                    {
                        "person_id": pid,
                        "user_id": seg["user_id"],
                        "name": seg["name"],
                        "vacant": False,
                        "col_key": f"{pid}-{seg['user_id']}",
                        "from_date": from_date,
                        "to_date": to_date,
                        "past": past,
                        "future": future,
                    }
                )
        elif has_position_history(db, pid):
            person_headers.append(
                {
                    "person_id": pid,
                    "user_id": None,
                    "name": "",
                    "vacant": True,
                    "col_key": f"{pid}-vacant",
                    "from_date": year_start,
                    "to_date": year_end,
                    "past": False,
                    "future": False,
                }
            )
        else:
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
