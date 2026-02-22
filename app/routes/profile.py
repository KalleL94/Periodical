# app/routes/profile.py
"""
Profile routes: user profile, wages, rates, vacation, absence.
"""

import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user, get_password_hash
from app.core.schedule import clear_schedule_cache
from app.core.utils import get_today
from app.database.database import Absence, AbsenceType, User, UserRole, get_db
from app.routes.shared import _parse_rates_form, templates

router = APIRouter(tags=["profile"])


@router.get("/profile", response_class=HTMLResponse, name="profile")
async def profile_page(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Show user profile page."""
    from app.core.rates import get_all_defaults, get_rate_history
    from app.core.schedule import get_wage_history
    from app.core.storage import get_available_tax_tables

    available_tax_tables = get_available_tax_tables()
    wage_history = get_wage_history(db, current_user.id)

    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "user": current_user,
            "available_tax_tables": available_tax_tables,
            "wage_history": wage_history,
            "rate_defaults": get_all_defaults(),
            "custom_rates": current_user.custom_rates or {},
            "rate_history": get_rate_history(db, current_user.id),
        },
    )


@router.post("/profile", name="profile_update")
async def update_profile(
    request: Request,
    name: str = Form(...),
    tax_table: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update user profile."""
    from app.core.rates import get_all_defaults, get_rate_history
    from app.core.storage import get_available_tax_tables

    available_tax_tables = get_available_tax_tables()
    if tax_table not in available_tax_tables:
        return templates.TemplateResponse(
            "profile.html",
            {
                "request": request,
                "user": current_user,
                "available_tax_tables": available_tax_tables,
                "error": f"Ogiltig skattetabell: {tax_table}",
                "rate_defaults": get_all_defaults(),
                "custom_rates": current_user.custom_rates or {},
                "rate_history": get_rate_history(db, current_user.id),
            },
            status_code=400,
        )

    current_user.name = name
    current_user.tax_table = tax_table
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    clear_schedule_cache()

    return RedirectResponse(url="/profile", status_code=302)


@router.post("/profile/password", name="change_password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change user password."""
    from app.auth.auth import verify_password
    from app.core.rates import get_all_defaults, get_rate_history

    def _profile_error(msg: str):
        return templates.TemplateResponse(
            "profile.html",
            {
                "request": request,
                "user": current_user,
                "error": msg,
                "rate_defaults": get_all_defaults(),
                "custom_rates": current_user.custom_rates or {},
                "rate_history": get_rate_history(db, current_user.id),
            },
            status_code=400,
        )

    if not verify_password(current_password, current_user.password_hash):
        return _profile_error("Fel nuvarande lösenord")

    if len(new_password) < 8:
        return _profile_error("Nytt lösenord måste vara minst 8 tecken")

    current_user.password_hash = get_password_hash(new_password)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return RedirectResponse(url="/profile", status_code=302)


@router.post("/profile/rates", name="profile_update_rates")
async def profile_update_rates(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """User: add new rate entry with effective date."""
    from app.core.rates import add_new_rates

    form = await request.form()
    rates = _parse_rates_form(form)
    effective_from = form.get("effective_from", "").strip()

    if not effective_from:
        raise HTTPException(status_code=400, detail="Från-datum krävs")

    try:
        effective_date = datetime.datetime.strptime(effective_from, "%Y-%m-%d").date()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Ogiltigt datum: {e}") from e

    add_new_rates(
        session=db,
        user_id=current_user.id,
        rates=rates,
        effective_from=effective_date,
        created_by=current_user.id,
    )
    clear_schedule_cache()

    return RedirectResponse(url="/profile", status_code=302)


@router.post("/profile/add-wage", name="profile_add_wage")
async def profile_add_wage(
    request: Request,
    new_wage: int = Form(...),
    effective_from: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """User: add a new wage with effective date."""
    from datetime import datetime

    from app.core.schedule import add_new_wage

    try:
        effective_date = datetime.strptime(effective_from, "%Y-%m-%d").date()
        add_new_wage(
            session=db,
            user_id=current_user.id,
            new_wage=new_wage,
            effective_from=effective_date,
            created_by=current_user.id,
        )
        clear_schedule_cache()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Ogiltigt datum: {e}") from e
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Kunde inte lägga till lön: {e}") from e

    return RedirectResponse(url="/profile", status_code=302)


@router.post("/profile/delete-wage/{wage_id}", name="profile_delete_wage")
async def profile_delete_wage(
    request: Request,
    wage_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """User: delete a wage history entry (only if it's their own and not the only one)."""
    from app.database.database import WageHistory

    wage_record = db.query(WageHistory).filter(WageHistory.id == wage_id).first()

    if not wage_record:
        raise HTTPException(status_code=404, detail="Wage record not found")

    if wage_record.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this wage record")

    total_wages = db.query(WageHistory).filter(WageHistory.user_id == current_user.id).count()

    if total_wages <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the only wage record")

    if wage_record.effective_to is None:
        previous_wage = (
            db.query(WageHistory)
            .filter(
                WageHistory.user_id == current_user.id,
                WageHistory.id != wage_id,
                WageHistory.effective_to.isnot(None),
            )
            .order_by(WageHistory.effective_from.desc())
            .first()
        )

        if previous_wage:
            previous_wage.effective_to = None
            current_user.wage = previous_wage.wage

    db.delete(wage_record)
    db.commit()
    clear_schedule_cache()

    return RedirectResponse(url="/profile", status_code=302)


@router.post("/profile/delete-rate/{rate_id}", name="profile_delete_rate")
async def profile_delete_rate(
    rate_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """User: delete a rate history entry (only their own)."""
    from app.core.rates import delete_rate_history

    delete_rate_history(db, rate_id, current_user.id)
    clear_schedule_cache()

    return RedirectResponse(url="/profile", status_code=302)


@router.get("/profile/calendar.ics/{lang}", response_class=Response, name="export_calendar")
async def export_calendar(
    current_user: User = Depends(get_current_user),
    lang: str = "sv",
) -> Response:
    """Exporterar användarens schema som iCal-fil."""
    from datetime import timedelta

    from app.core.calendar_export import generate_ical

    if lang not in ["sv", "en"]:
        raise HTTPException(status_code=400, detail="Ogiltigt språk")

    start_date = get_today()
    end_date = start_date + timedelta(days=180)

    ical_content = generate_ical(person_id=current_user.id, start_date=start_date, end_date=end_date, lang=lang)

    return Response(
        content=ical_content,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="schema.ics"',
        },
    )


# ============ Vacation Routes ============


@router.get("/profile/vacation", response_class=HTMLResponse, name="vacation_page")
async def vacation_page(
    request: Request,
    year: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Show vacation management page."""
    from app.core.schedule.vacation import calculate_vacation_balance
    from app.database.database import Absence, AbsenceType

    if year is None:
        year = get_today().year

    vacation = current_user.vacation or {}
    vacation_weeks = vacation.get(str(year), [])

    balance = calculate_vacation_balance(current_user, year, db)

    day_absences = (
        db.query(Absence)
        .filter(
            Absence.user_id == current_user.id,
            Absence.absence_type == AbsenceType.VACATION,
            Absence.date >= datetime.date(year, 1, 1),
            Absence.date <= datetime.date(year, 12, 31),
        )
        .order_by(Absence.date)
        .all()
    )

    return templates.TemplateResponse(
        "vacation.html",
        {
            "request": request,
            "user": current_user,
            "year": year,
            "vacation_weeks": vacation_weeks,
            "balance": balance,
            "day_absences": day_absences,
        },
    )


@router.post("/profile/vacation", name="update_vacation")
async def update_vacation(
    request: Request,
    year: int = Form(...),
    weeks: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update vacation weeks for a year."""
    if not (2020 <= year <= 2100):
        return templates.TemplateResponse(
            "vacation.html",
            {
                "request": request,
                "user": current_user,
                "year": year,
                "vacation_weeks": current_user.vacation.get(str(year), []) if current_user.vacation else [],
                "error": "Ogiltigt år: måste vara mellan 2020 och 2100",
            },
            status_code=400,
        )

    if weeks.strip():
        week_list = [int(w.strip()) for w in weeks.split(",") if w.strip().isdigit()]
    else:
        week_list = []

    week_list = [w for w in week_list if 1 <= w <= 53]
    week_list = sorted(set(week_list))

    vacation = current_user.vacation or {}
    vacation[str(year)] = week_list
    current_user.vacation = vacation

    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(current_user, "vacation")

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    clear_schedule_cache()

    return RedirectResponse(url=f"/profile/vacation?year={year}", status_code=302)


@router.post("/profile/vacation/day", name="add_vacation_day")
async def add_vacation_day(
    vacation_date: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add a single vacation day for current user."""
    try:
        d = datetime.date.fromisoformat(vacation_date)
    except ValueError:
        return RedirectResponse(url="/profile/vacation", status_code=302)

    existing = db.query(Absence).filter(Absence.user_id == current_user.id, Absence.date == d).first()
    if existing:
        existing.absence_type = AbsenceType.VACATION
    else:
        db.add(
            Absence(
                user_id=current_user.id,
                date=d,
                absence_type=AbsenceType.VACATION,
            )
        )

    db.commit()
    clear_schedule_cache()
    return RedirectResponse(url=f"/profile/vacation?year={d.year}", status_code=302)


@router.post("/profile/vacation/days/sync", name="sync_vacation_days")
async def sync_vacation_days(
    year: int = Form(...),
    dates: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sync day-level vacation for a year. Adds new, removes deselected."""
    new_dates: set[datetime.date] = set()
    for s in dates.split(","):
        s = s.strip()
        if s:
            try:
                new_dates.add(datetime.date.fromisoformat(s))
            except ValueError:
                continue

    existing = (
        db.query(Absence)
        .filter(
            Absence.user_id == current_user.id,
            Absence.absence_type == AbsenceType.VACATION,
            Absence.date >= datetime.date(year, 1, 1),
            Absence.date <= datetime.date(year, 12, 31),
        )
        .all()
    )
    existing_dates = {a.date: a for a in existing}

    for d in new_dates - set(existing_dates.keys()):
        db.add(Absence(user_id=current_user.id, date=d, absence_type=AbsenceType.VACATION))

    for d in set(existing_dates.keys()) - new_dates:
        db.delete(existing_dates[d])

    db.commit()
    clear_schedule_cache()
    return RedirectResponse(url=f"/profile/vacation?year={year}", status_code=302)


@router.post("/profile/vacation/day/{absence_id}/delete", name="delete_vacation_day")
async def delete_vacation_day(
    absence_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a single vacation day for current user."""
    absence = (
        db.query(Absence)
        .filter(
            Absence.id == absence_id,
            Absence.user_id == current_user.id,
            Absence.absence_type == AbsenceType.VACATION,
        )
        .first()
    )
    year = get_today().year
    if absence:
        year = absence.date.year
        db.delete(absence)
        db.commit()
        clear_schedule_cache()
    return RedirectResponse(url=f"/profile/vacation?year={year}", status_code=302)


@router.post("/profile/vacation/settings", name="update_vacation_settings")
async def update_vacation_settings(
    employment_start_date: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update employment start date for current user."""
    if employment_start_date.strip():
        try:
            current_user.employment_start_date = datetime.date.fromisoformat(employment_start_date)
        except ValueError:
            pass
    else:
        current_user.employment_start_date = None

    db.commit()
    clear_schedule_cache()
    return RedirectResponse(url="/profile/vacation", status_code=302)


# ============ Absence Routes ============


@router.post("/absence/add", name="add_absence")
async def add_absence(
    request: Request,
    user_id: int = Form(...),
    date_str: str = Form(..., alias="date"),
    absence_type: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add absence for the current user."""
    from datetime import datetime

    if current_user.role != UserRole.ADMIN and user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to add absence for other users")

    try:
        absence_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Ogiltigt datumformat. Använd YYYY-MM-DD") from None

    try:
        absence_type_enum = AbsenceType(absence_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Ogiltig frånvarotyp: {absence_type}") from None

    if current_user.role == UserRole.ADMIN:
        target_user_id = user_id
    else:
        target_user_id = current_user.id

    existing = db.query(Absence).filter(Absence.user_id == target_user_id, Absence.date == absence_date).first()

    if existing:
        existing.absence_type = absence_type_enum
        db.commit()
    else:
        new_absence = Absence(user_id=target_user_id, date=absence_date, absence_type=absence_type_enum)
        db.add(new_absence)
        db.commit()

    clear_schedule_cache()

    return RedirectResponse(
        url=f"/day/{target_user_id}/{absence_date.year}/{absence_date.month}/{absence_date.day}", status_code=302
    )


@router.post("/absence/{absence_id}/delete", name="delete_absence")
async def delete_absence(
    request: Request,
    absence_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete an absence record."""
    absence = db.query(Absence).filter(Absence.id == absence_id).first()

    if not absence:
        raise HTTPException(status_code=404, detail="Frånvaro hittades inte")

    if absence.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Du kan bara ta bort din egen frånvaro")

    absence_date = absence.date
    absence_user_id = absence.user_id

    db.delete(absence)
    db.commit()

    clear_schedule_cache()

    return RedirectResponse(
        url=f"/day/{absence_user_id}/{absence_date.year}/{absence_date.month}/{absence_date.day}", status_code=302
    )
