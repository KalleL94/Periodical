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
from app.database.database import (
    Absence,
    AbsenceType,
    OvertimeShift,
    Substitute,
    SubstituteShift,
    User,
    get_db,
)
from app.routes.shared import render

router = APIRouter(tags=["substitutes"])

_ALLOWED_CODES = {"N1", "N2", "N3", "OC"}
# Absence types selectable for substitutes (we only track days, no pay).
_ABSENCE_TYPES = ["SICK", "VAB", "LEAVE", "OFF", "PARENTAL", "VACATION"]


@router.get("/admin/substitutes", response_class=HTMLResponse, name="admin_substitutes")
async def admin_substitutes_page(
    request: Request,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: list all substitutes."""
    substitutes = db.query(Substitute).order_by(Substitute.is_active.desc(), Substitute.name).all()
    # Names of linked user accounts, for the link-status column
    linked_ids = [s.user_id for s in substitutes if s.user_id is not None]
    linked_names = {}
    if linked_ids:
        users = db.query(User).filter(User.id.in_(linked_ids)).all()
        by_id = {u.id: u.name for u in users}
        linked_names = {s.id: by_id.get(s.user_id, "") for s in substitutes if s.user_id is not None}
    return render(
        "admin_substitutes.html",
        {"request": request, "user": current_user, "substitutes": substitutes, "linked_names": linked_names},
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

    # Overtime and absence entries for the displayed month (substitutes track hours/days only)
    overtime_shifts = (
        db.query(OvertimeShift)
        .filter(
            OvertimeShift.substitute_id == substitute_id,
            OvertimeShift.date >= first_day,
            OvertimeShift.date <= last_day,
        )
        .order_by(OvertimeShift.date)
        .all()
    )
    absences = (
        db.query(Absence)
        .filter(
            Absence.substitute_id == substitute_id,
            Absence.date >= first_day,
            Absence.date <= last_day,
        )
        .order_by(Absence.date)
        .all()
    )

    prev_month = month - 1 or 12
    prev_year = year - 1 if month == 1 else year
    next_month = month + 1 if month < 12 else 1
    next_year = year + 1 if month == 12 else year

    # Users selectable for the account link (issue #290)
    link_users = db.query(User).order_by(User.name).all()

    return render(
        "admin_substitute_manage.html",
        {
            "request": request,
            "user": current_user,
            "substitute": substitute,
            "link_users": link_users,
            "weeks": weeks,
            "shift_by_date": shift_by_date,
            "overtime_shifts": overtime_shifts,
            "absences": absences,
            "absence_types": _ABSENCE_TYPES,
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


def _require_substitute(db: Session, substitute_id: int) -> Substitute:
    substitute = db.query(Substitute).filter(Substitute.id == substitute_id).first()
    if not substitute:
        raise HTTPException(status_code=404, detail="Substitute not found")
    return substitute


@router.post("/admin/substitutes/{substitute_id}/link", name="admin_substitute_link")
async def admin_substitute_link(
    substitute_id: int,
    user_id: str = Form(""),
    hourly_wage: str = Form(""),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: link/unlink a substitute to a user account and set the hourly wage.

    Linking retroactively makes the substitute's pre-employment shifts render
    and price in the linked user's personal views (issue #290). An empty user
    selection unlinks; an empty wage clears it.
    """
    substitute = _require_substitute(db, substitute_id)

    if user_id.strip():
        try:
            uid = int(user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user id") from None
        user = db.query(User).filter(User.id == uid).first()
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        substitute.user_id = uid
    else:
        substitute.user_id = None

    if hourly_wage.strip():
        try:
            wage = int(hourly_wage)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid hourly wage") from None
        if wage <= 0:
            raise HTTPException(status_code=400, detail="Invalid hourly wage")
        substitute.hourly_wage = wage
    else:
        substitute.hourly_wage = None

    db.commit()
    clear_schedule_cache()

    return RedirectResponse(url=f"/admin/substitutes/{substitute_id}", status_code=303)


@router.post("/admin/substitutes/{substitute_id}/overtime/add", name="admin_substitute_overtime_add")
async def admin_substitute_overtime_add(
    substitute_id: int,
    date: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    hours: float = Form(...),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Add an overtime entry for a substitute (hours only; ot_pay is always 0)."""
    _require_substitute(db, substitute_id)

    from datetime import datetime as _dt

    ot_date = _dt.strptime(date, "%Y-%m-%d").date()
    start_t = _dt.strptime(start_time, "%H:%M").time()
    end_t = _dt.strptime(end_time, "%H:%M").time()

    db.add(
        OvertimeShift(
            substitute_id=substitute_id,
            date=ot_date,
            start_time=start_t,
            end_time=end_t,
            hours=hours,
            ot_pay=0.0,
            is_extension=False,
            created_by=current_user.id,
        )
    )
    db.commit()
    clear_schedule_cache()

    return RedirectResponse(
        url=f"/admin/substitutes/{substitute_id}?year={ot_date.year}&month={ot_date.month}", status_code=303
    )


@router.post("/admin/substitutes/{substitute_id}/overtime/{ot_id}/delete", name="admin_substitute_overtime_delete")
async def admin_substitute_overtime_delete(
    substitute_id: int,
    ot_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Delete an overtime entry belonging to a substitute."""
    ot = db.query(OvertimeShift).filter(OvertimeShift.id == ot_id, OvertimeShift.substitute_id == substitute_id).first()
    if not ot:
        raise HTTPException(status_code=404, detail="Overtime entry not found")
    year, month = ot.date.year, ot.date.month
    db.delete(ot)
    db.commit()
    clear_schedule_cache()

    return RedirectResponse(url=f"/admin/substitutes/{substitute_id}?year={year}&month={month}", status_code=303)


@router.post("/admin/substitutes/{substitute_id}/absence/add", name="admin_substitute_absence_add")
async def admin_substitute_absence_add(
    substitute_id: int,
    date: str = Form(...),
    absence_type: str = Form(...),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Add an absence day for a substitute (day tracking only, no deduction)."""
    _require_substitute(db, substitute_id)

    if absence_type not in _ABSENCE_TYPES:
        raise HTTPException(status_code=400, detail="Invalid absence type")

    from datetime import datetime as _dt

    abs_date = _dt.strptime(date, "%Y-%m-%d").date()

    existing = db.query(Absence).filter(Absence.substitute_id == substitute_id, Absence.date == abs_date).first()
    if existing:
        existing.absence_type = AbsenceType(absence_type)
    else:
        db.add(
            Absence(
                substitute_id=substitute_id,
                date=abs_date,
                absence_type=AbsenceType(absence_type),
            )
        )
    db.commit()
    clear_schedule_cache()

    return RedirectResponse(
        url=f"/admin/substitutes/{substitute_id}?year={abs_date.year}&month={abs_date.month}", status_code=303
    )


@router.post("/admin/substitutes/{substitute_id}/absence/{absence_id}/delete", name="admin_substitute_absence_delete")
async def admin_substitute_absence_delete(
    substitute_id: int,
    absence_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Delete an absence day belonging to a substitute."""
    absence = db.query(Absence).filter(Absence.id == absence_id, Absence.substitute_id == substitute_id).first()
    if not absence:
        raise HTTPException(status_code=404, detail="Absence not found")
    year, month = absence.date.year, absence.date.month
    db.delete(absence)
    db.commit()
    clear_schedule_cache()

    return RedirectResponse(url=f"/admin/substitutes/{substitute_id}?year={year}&month={month}", status_code=303)
