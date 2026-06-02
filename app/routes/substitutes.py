# app/routes/substitutes.py
"""Admin routes for managing summer substitutes (vikarier) and their shifts.

Substitutes are not login users; they have no rotation and no salary. Their shifts
are entered manually here via a month calendar and only appear in the week/month
all-person schedules.
"""

import calendar as _calendar
from datetime import date as date_cls
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_admin_user
from app.core.schedule import clear_schedule_cache
from app.core.utils import get_today
from app.database.database import Substitute, SubstituteShift, User, get_db
from app.routes.shared import render

router = APIRouter(tags=["substitutes"])

_ALLOWED_CODES = {"N1", "N2", "N3", "OC"}


@router.get("/admin/substitutes", response_class=HTMLResponse, name="admin_substitutes")
async def admin_substitutes_page(
    request: Request,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: list all substitutes."""
    substitutes = db.query(Substitute).order_by(Substitute.is_active.desc(), Substitute.name).all()
    return render(
        "admin_substitutes.html",
        {"request": request, "user": current_user, "substitutes": substitutes},
    )


@router.post("/admin/substitutes/create", name="admin_substitute_create")
async def admin_substitute_create(
    request: Request,
    name: str = Form(...),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: create a new substitute (name only)."""
    name = name.strip()
    if not name:
        substitutes = db.query(Substitute).order_by(Substitute.is_active.desc(), Substitute.name).all()
        return render(
            "admin_substitutes.html",
            {"request": request, "user": current_user, "substitutes": substitutes, "error": "Namn krävs"},
            status_code=400,
        )

    substitute = Substitute(name=name, is_active=1, created_by=current_user.id)
    db.add(substitute)
    db.commit()

    return RedirectResponse(url=f"/admin/substitutes/{substitute.id}", status_code=303)


@router.get("/admin/substitutes/{substitute_id}", response_class=HTMLResponse, name="admin_substitute_manage")
async def admin_substitute_manage_page(
    request: Request,
    substitute_id: int,
    year: int | None = None,
    month: int | None = None,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: manage a substitute via a month calendar."""
    substitute = db.query(Substitute).filter(Substitute.id == substitute_id).first()
    if not substitute:
        raise HTTPException(status_code=404, detail="Substitute not found")

    today = get_today()
    year = year or today.year
    month = month or today.month

    # Calendar weeks of date objects (includes adjacent-month padding days)
    weeks = _calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)

    # Map existing shifts for the displayed month for pre-selection
    first_day = date_cls(year, month, 1)
    last_day = date_cls(year, month, _calendar.monthrange(year, month)[1])
    shifts = (
        db.query(SubstituteShift)
        .filter(
            SubstituteShift.substitute_id == substitute_id,
            SubstituteShift.date >= first_day,
            SubstituteShift.date <= last_day,
        )
        .all()
    )
    shift_by_date = {s.date.isoformat(): s.shift_code for s in shifts}

    prev_month = month - 1 or 12
    prev_year = year - 1 if month == 1 else year
    next_month = month + 1 if month < 12 else 1
    next_year = year + 1 if month == 12 else year

    return render(
        "admin_substitute_manage.html",
        {
            "request": request,
            "user": current_user,
            "substitute": substitute,
            "weeks": weeks,
            "shift_by_date": shift_by_date,
            "year": year,
            "month": month,
            "allowed_codes": ["N1", "N2", "N3", "OC"],
            "prev_year": prev_year,
            "prev_month": prev_month,
            "next_year": next_year,
            "next_month": next_month,
        },
    )


@router.post("/admin/substitutes/{substitute_id}/toggle", name="admin_substitute_toggle")
async def admin_substitute_toggle(
    substitute_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: archive/restore a substitute (archived substitutes are hidden from schedules)."""
    substitute = db.query(Substitute).filter(Substitute.id == substitute_id).first()
    if not substitute:
        raise HTTPException(status_code=404, detail="Substitute not found")

    substitute.is_active = 0 if substitute.is_active else 1
    db.commit()
    clear_schedule_cache()

    return RedirectResponse(url="/admin/substitutes", status_code=303)


@router.post("/admin/substitutes/{substitute_id}/save", name="admin_substitute_save")
async def admin_substitute_save(
    request: Request,
    substitute_id: int,
    year: int = Form(...),
    month: int = Form(...),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: save the whole displayed month from the calendar.

    For each day in the month a `shift_<iso-date>` field is submitted. An empty value
    (or "OFF") removes any existing shift; a valid code upserts it. Only days within the
    submitted month are touched, so other months are never affected.
    """
    substitute = db.query(Substitute).filter(Substitute.id == substitute_id).first()
    if not substitute:
        raise HTTPException(status_code=404, detail="Substitute not found")

    form = await request.form()
    first_day = date_cls(year, month, 1)
    last_day = date_cls(year, month, _calendar.monthrange(year, month)[1])

    existing = {
        s.date: s
        for s in db.query(SubstituteShift).filter(
            SubstituteShift.substitute_id == substitute_id,
            SubstituteShift.date >= first_day,
            SubstituteShift.date <= last_day,
        )
    }

    current = first_day
    while current <= last_day:
        value = (form.get(f"shift_{current.isoformat()}") or "").strip()
        row = existing.get(current)
        if value in _ALLOWED_CODES:
            if row:
                row.shift_code = value
                row.created_by = current_user.id
            else:
                db.add(
                    SubstituteShift(
                        substitute_id=substitute_id,
                        date=current,
                        shift_code=value,
                        created_by=current_user.id,
                    )
                )
        elif row:
            # Empty / OFF: clear the day
            db.delete(row)
        current += timedelta(days=1)

    db.commit()
    clear_schedule_cache()

    return RedirectResponse(url=f"/admin/substitutes/{substitute_id}?year={year}&month={month}", status_code=303)
