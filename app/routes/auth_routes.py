# app/routes/auth_routes.py
"""
Authentication routes: login, logout, registration.
"""

import datetime
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.auth import (
    authenticate_user,
    clear_auth_cookie,
    create_access_token,
    get_admin_user,
    get_current_user,
    get_current_user_optional,
    get_password_hash,
    get_user_by_username,
    set_auth_cookie,
)
from app.core.constants import DEFAULT_PASSWORD
from app.core.logging_config import get_logger
from app.core.request_logging import log_auth_event
from app.core.schedule import clear_schedule_cache
from app.core.sentry_config import add_breadcrumb, clear_user_context, set_user_context
from app.core.utils import get_today
from app.database.database import Absence, AbsenceType, User, UserRole, get_db
from app.routes.shared import templates

logger = get_logger(__name__)

router = APIRouter(tags=["auth"])


# ============ Pydantic schemas ============


class UserCreate(BaseModel):
    username: str
    password: str
    name: str
    wage: int
    role: UserRole = UserRole.USER


class UserUpdate(BaseModel):
    name: str | None = None
    wage: int | None = None
    vacation: dict | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


def is_safe_redirect(url: str) -> bool:
    """Check if redirect URL is safe (local path only)."""
    if not url:
        return False
    parsed = urlparse(url)
    # Only allow relative paths (no scheme or netloc), and prevent scheme-relative URLs
    return not parsed.scheme and not parsed.netloc and url.startswith("/") and not url.startswith("//")


# ============ HTML Routes ============


@router.get("/login", response_class=HTMLResponse, name="login_page")
async def login_page(
    request: Request,
    next: str | None = Query(None),
    current_user: User | None = Depends(get_current_user_optional),
):
    """Show login form."""
    if current_user:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(
        "login.html", {"request": request, "next": next if is_safe_redirect(next) else None}
    )


@router.post("/login", name="login")
async def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(None),
    db: Session = Depends(get_db),
):
    """Process login form."""
    user = authenticate_user(db, username, password)
    if not user:
        # Log failed login attempt
        log_auth_event(
            event_type="login",
            username=username,
            success=False,
            details={"ip": request.client.host if request.client else "unknown"},
        )

        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Fel användarnamn eller lösenord", "next": next},
            status_code=401,
        )

    # Log successful login
    log_auth_event(
        event_type="login",
        username=user.username,
        user_id=user.id,
        success=True,
        details={
            "ip": request.client.host if request.client else "unknown",
            "must_change_password": user.must_change_password == 1,
        },
    )

    # Set Sentry user context for error tracking
    set_user_context(user_id=user.id, username=user.username)
    add_breadcrumb(message=f"User {user.username} logged in", category="auth", level="info")

    # Create access token and set cookie
    access_token = create_access_token(data={"sub": str(user.id)})

    # Check if user must change password
    if user.must_change_password == 1:
        redirect = RedirectResponse(url="/change-password", status_code=302)
        set_auth_cookie(redirect, access_token)
        return redirect

    # Redirect to next URL if safe, otherwise home
    redirect_url = next if is_safe_redirect(next) else "/"
    redirect = RedirectResponse(url=redirect_url, status_code=302)
    set_auth_cookie(redirect, access_token)
    return redirect


@router.get("/logout", name="logout")
async def logout(response: Response, current_user: User | None = Depends(get_current_user_optional)):
    """Log out user."""
    # Log logout
    if current_user:
        log_auth_event(
            event_type="logout",
            username=current_user.username,
            user_id=current_user.id,
            success=True,
        )
        # Clear Sentry user context
        clear_user_context()

    redirect = RedirectResponse(url="/login", status_code=302)
    clear_auth_cookie(redirect)
    return redirect


@router.get("/change-password", response_class=HTMLResponse, name="change_password_page")
async def change_password_page(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Show mandatory password change page for users with must_change_password=1."""
    return templates.TemplateResponse(
        "change_password.html",
        {
            "request": request,
            "user": current_user,
            "must_change": current_user.must_change_password == 1,
        },
    )


@router.post("/change-password", name="change_password_submit")
async def change_password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Process mandatory password change."""
    from app.auth.auth import verify_password

    # Validate current password
    if not verify_password(current_password, current_user.password_hash):
        return templates.TemplateResponse(
            "change_password.html",
            {
                "request": request,
                "user": current_user,
                "must_change": current_user.must_change_password == 1,
                "error": "Fel nuvarande lösenord",
            },
            status_code=400,
        )

    # Validate new password matches confirmation
    if new_password != confirm_password:
        return templates.TemplateResponse(
            "change_password.html",
            {
                "request": request,
                "user": current_user,
                "must_change": current_user.must_change_password == 1,
                "error": "Nya lösenordet matchar inte bekräftelsen",
            },
            status_code=400,
        )

    # Validate new password is different from old
    if verify_password(new_password, current_user.password_hash):
        return templates.TemplateResponse(
            "change_password.html",
            {
                "request": request,
                "user": current_user,
                "must_change": current_user.must_change_password == 1,
                "error": "Nytt lösenord måste vara annorlunda än det gamla",
            },
            status_code=400,
        )

    # Validate new password strength (minimum 8 characters)
    if len(new_password) < 8:
        return templates.TemplateResponse(
            "change_password.html",
            {
                "request": request,
                "user": current_user,
                "must_change": current_user.must_change_password == 1,
                "error": "Nytt lösenord måste vara minst 8 tecken",
            },
            status_code=400,
        )

    # Update password and clear must_change_password flag
    current_user.password_hash = get_password_hash(new_password)
    was_forced = current_user.must_change_password == 1
    current_user.must_change_password = 0

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    clear_schedule_cache()

    # Log password change
    log_auth_event(
        event_type="password_change",
        username=current_user.username,
        user_id=current_user.id,
        success=True,
        details={"forced": was_forced},
    )

    # Redirect to home page
    return RedirectResponse(url="/", status_code=302)


@router.get("/profile", response_class=HTMLResponse, name="profile")
async def profile_page(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Show user profile page."""
    from app.core.schedule import get_wage_history
    from app.core.storage import get_available_tax_tables

    available_tax_tables = get_available_tax_tables()

    # Get wage history for current user
    wage_history = get_wage_history(db, current_user.id)

    from app.core.rates import get_all_defaults, get_rate_history

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

    # Validate tax table
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

    # Add length validation
    if len(new_password) < 8:
        return _profile_error("Nytt lösenord måste vara minst 8 tecken")

    current_user.password_hash = get_password_hash(new_password)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

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
        # Parse effective_from date
        effective_date = datetime.strptime(effective_from, "%Y-%m-%d").date()

        # Add new wage
        add_new_wage(
            session=db,
            user_id=current_user.id,
            new_wage=new_wage,
            effective_from=effective_date,
            created_by=current_user.id,
        )

        # Clear schedule cache to update calculations
        clear_schedule_cache()

    except ValueError as e:
        # Invalid date format
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

    # Get the wage record
    wage_record = db.query(WageHistory).filter(WageHistory.id == wage_id).first()

    if not wage_record:
        raise HTTPException(status_code=404, detail="Wage record not found")

    # Security check: ensure user can only delete their own wages
    if wage_record.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this wage record")

    # Check if this is the only wage record
    total_wages = db.query(WageHistory).filter(WageHistory.user_id == current_user.id).count()

    if total_wages <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the only wage record")

    # If this was the current wage (effective_to is NULL), we need to reopen the previous wage
    if wage_record.effective_to is None:
        # Find the previous wage (the one that ends just before this one starts)
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
            # Reopen the previous wage (set effective_to to NULL)
            previous_wage.effective_to = None

            # Update User.wage to the previous wage
            current_user.wage = previous_wage.wage

    # Delete the wage record
    db.delete(wage_record)
    db.commit()

    # Clear schedule cache
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


@router.get("/profile/vacation", response_class=HTMLResponse, name="vacation_page")
async def vacation_page(
    request: Request,
    year: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Show vacation management page."""
    from app.core.schedule.vacation import calculate_vacation_balance

    if year is None:
        year = get_today().year

    vacation = current_user.vacation or {}
    vacation_weeks = vacation.get(str(year), [])

    # Calculate balance
    balance = calculate_vacation_balance(current_user, year, db)

    # Get day-level vacation for this year
    from app.database.database import Absence, AbsenceType

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
    weeks: str = Form(""),  # Comma-separated week numbers
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update vacation weeks for a year."""
    # Validate year range
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

    # Parse weeks from form
    if weeks.strip():
        week_list = [int(w.strip()) for w in weeks.split(",") if w.strip().isdigit()]
    else:
        week_list = []

    # Validate week numbers
    week_list = [w for w in week_list if 1 <= w <= 53]
    week_list = sorted(set(week_list))

    # Update vacation
    vacation = current_user.vacation or {}
    vacation[str(year)] = week_list
    current_user.vacation = vacation

    # SQLAlchemy needs this to detect JSON changes
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

    # Check if absence already exists for this date
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


@router.get("/profile/calendar.ics/{lang}", response_class=Response, name="export_calendar")
async def export_calendar(
    current_user: User = Depends(get_current_user),
    lang: str = "sv",
) -> Response:
    """
    Exporterar användarens schema som iCal-fil.
    Genererar en kalender för de närmaste 6 månaderna.
    """
    from datetime import timedelta

    from app.core.calendar_export import generate_ical

    # Validera språk
    if lang not in ["sv", "en"]:
        raise HTTPException(status_code=400, detail="Ogiltigt språk")

    # Beräkna datumintervall (6 månader framåt)
    start_date = get_today()
    end_date = start_date + timedelta(days=180)  # ~6 månader

    # Generera iCal
    ical_content = generate_ical(person_id=current_user.id, start_date=start_date, end_date=end_date, lang=lang)

    # Returnera som nedladdningsbar fil
    return Response(
        content=ical_content,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="schema.ics"',
        },
    )


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

    # Permission check
    if current_user.role != UserRole.ADMIN and user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to add absence for other users")

    # Parse date
    try:
        absence_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Ogiltigt datumformat. Använd YYYY-MM-DD") from None

    # Validate absence type
    try:
        absence_type_enum = AbsenceType(absence_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Ogiltig frånvarotyp: {absence_type}") from None

    if current_user.role == UserRole.ADMIN:
        # Admin adding absence for another user
        target_user_id = user_id
    else:
        # Regular user adding absence for self
        target_user_id = current_user.id

    # Check if absence already exists for this date
    existing = db.query(Absence).filter(Absence.user_id == target_user_id, Absence.date == absence_date).first()

    if existing:
        # Update existing absence type
        existing.absence_type = absence_type_enum
        db.commit()
    else:
        # Create new absence
        new_absence = Absence(user_id=target_user_id, date=absence_date, absence_type=absence_type_enum)
        db.add(new_absence)
        db.commit()

    # Clear schedule cache to reflect changes
    clear_schedule_cache()

    # Redirect back to day view
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
    # Find the absence
    absence = db.query(Absence).filter(Absence.id == absence_id).first()

    if not absence:
        raise HTTPException(status_code=404, detail="Frånvaro hittades inte")

    # Check if user owns this absence
    if absence.user_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Du kan bara ta bort din egen frånvaro")

    # Store date for redirect
    absence_date = absence.date
    absence_user_id = absence.user_id

    # Delete the absence
    db.delete(absence)
    db.commit()

    # Clear schedule cache to reflect changes
    clear_schedule_cache()

    # Redirect back to day view
    return RedirectResponse(
        url=f"/day/{absence_user_id}/{absence_date.year}/{absence_date.month}/{absence_date.day}", status_code=302
    )


# ============ Admin Routes ============


@router.get("/admin/users", response_class=HTMLResponse, name="admin_users")
async def admin_users_page(
    request: Request,
    success: str | None = Query(None),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: list all users."""
    users = db.query(User).order_by(User.id).all()
    return templates.TemplateResponse(
        "admin_users.html",
        {
            "request": request,
            "user": current_user,
            "users": users,
            "success": success,
        },
    )


@router.get("/admin/users/create", response_class=HTMLResponse, name="admin_create_user_page")
async def admin_create_user_page(
    request: Request,
    current_user: User = Depends(get_admin_user),
):
    """Admin: show create user form."""
    return templates.TemplateResponse(
        "admin_user_create.html",
        {
            "request": request,
            "user": current_user,
        },
    )


@router.post("/admin/users/create", name="admin_create_user")
async def admin_create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    name: str = Form(...),
    wage: int = Form(...),
    role: str = Form("user"),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: create new user."""
    # Check if username exists
    if get_user_by_username(db, username):
        return templates.TemplateResponse(
            "admin_user_create.html",
            {"request": request, "user": current_user, "error": "Användarnamnet finns redan"},
            status_code=400,
        )

    new_user = User(
        username=username,
        password_hash=get_password_hash(password),
        name=name,
        wage=wage,
        role=UserRole(role),
        vacation={},
        must_change_password=1,  # Force password change on first login
    )
    db.add(new_user)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return RedirectResponse(url="/admin/users", status_code=302)


@router.get("/admin/users/{user_id}", response_class=HTMLResponse, name="admin_edit_user_page")
async def admin_edit_user_page(
    request: Request,
    user_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: show edit user form."""
    from app.core.schedule import get_wage_history
    from app.core.schedule.person_history import get_user_history
    from app.core.schedule.vacation import calculate_vacation_balance
    from app.core.storage import get_available_tax_tables
    from app.core.utils import get_today

    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")

    available_tax_tables = get_available_tax_tables()

    # Get wage history for this user
    wage_history = get_wage_history(db, user_id)

    # Get person history for this user (employment periods)
    person_history = get_user_history(db, user_id)

    # Get vacation balance
    vacation_balance = calculate_vacation_balance(edit_user, get_today().year, db)

    from app.core.rates import get_all_defaults, get_rate_history
    from app.core.schedule.transition import (
        calculate_consultant_vacation_days,
        calculate_variable_avg_daily,
        get_earning_year,
    )
    from app.database.database import EmploymentTransition

    edit_transition = db.query(EmploymentTransition).filter(EmploymentTransition.user_id == edit_user.id).first()
    admin_auto_variable_avg = None
    admin_auto_vacation_days = None
    if edit_transition:
        if edit_transition.variable_avg_daily_override is None:
            earning_start, earning_end = get_earning_year(edit_transition)
            admin_auto_variable_avg = calculate_variable_avg_daily(edit_user, db, earning_start, earning_end)
        admin_auto_vacation_days = calculate_consultant_vacation_days(edit_user, edit_transition)

    return templates.TemplateResponse(
        "admin_user_edit.html",
        {
            "request": request,
            "user": current_user,
            "edit_user": edit_user,
            "available_tax_tables": available_tax_tables,
            "wage_history": wage_history,
            "person_history": person_history,
            "vacation_balance": vacation_balance,
            "rate_defaults": get_all_defaults(),
            "custom_rates": edit_user.custom_rates or {},
            "rate_history": get_rate_history(db, edit_user.id),
            "edit_transition": edit_transition,
            "admin_auto_variable_avg": admin_auto_variable_avg,
            "admin_auto_vacation_days": admin_auto_vacation_days,
            "salary_types": [
                ("trailing", "Släpande (lön för föregående månad)"),
                ("current", "Innestående (lön för aktuell månad)"),
            ],
        },
    )


@router.post("/admin/users/{user_id}", name="admin_update_user")
async def admin_update_user(
    request: Request,
    user_id: int,
    name: str = Form(...),
    role: str = Form("user"),
    person_id: int | None = Form(None),
    tax_table: str | None = Form(None),
    new_password: str = Form(None),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: update user."""
    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")

    edit_user.name = name
    edit_user.role = UserRole(role)
    edit_user.person_id = person_id  # Can be None or 1-10
    edit_user.tax_table = tax_table if tax_table else None

    if new_password:
        # Add length validation
        if len(new_password) < 8:
            return templates.TemplateResponse(
                "admin_user_edit.html",
                {
                    "request": request,
                    "user": current_user,
                    "edit_user": edit_user,
                    "error": "Nytt lösenord måste vara minst 8 tecken",
                },
                status_code=400,
            )
        edit_user.password_hash = get_password_hash(new_password)
        edit_user.must_change_password = 1  # Force password change when admin sets new password

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return RedirectResponse(url="/admin/users", status_code=302)


@router.post("/admin/users/{user_id}/transition", name="admin_transition_save")
async def admin_transition_save(
    request: Request,
    user_id: int,
    transition_date: str = Form(...),
    consultant_salary_type: str = Form(...),
    consultant_vacation_days: str = Form(""),
    consultant_supplement_pct: float = Form(...),
    variable_avg_daily_override: str = Form(""),
    earning_year_start: str = Form(""),
    earning_year_end: str = Form(""),
    notes: str = Form(""),
    new_direct_salary: str = Form(""),
    reset_rates_to_default: str = Form(""),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: spara anställningsövergång för en användare."""
    import datetime as _dt

    from app.database.database import ConsultantSalaryType, EmploymentTransition

    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        t_date = _dt.date.fromisoformat(transition_date)
    except ValueError:
        return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)

    if consultant_salary_type not in ("trailing", "current"):
        return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)

    salary_type = ConsultantSalaryType(consultant_salary_type)

    # Semesterdagar: manuell override eller auto-beräknat
    if consultant_vacation_days.strip():
        try:
            parsed_vacation_days = float(consultant_vacation_days.strip())
        except ValueError:
            parsed_vacation_days = 0.0
    else:
        from types import SimpleNamespace

        from app.core.schedule.transition import calculate_consultant_vacation_days

        temp = SimpleNamespace(
            transition_date=t_date,
            earning_year_start=None,
            earning_year_end=None,
        )
        parsed_vacation_days = float(calculate_consultant_vacation_days(edit_user, temp) or 0)

    variable_override: float | None = None
    if variable_avg_daily_override.strip():
        try:
            variable_override = float(variable_avg_daily_override.strip())
        except ValueError:
            pass

    earning_start: _dt.date | None = None
    earning_end: _dt.date | None = None
    if earning_year_start.strip():
        try:
            earning_start = _dt.date.fromisoformat(earning_year_start.strip())
        except ValueError:
            pass
    if earning_year_end.strip():
        try:
            earning_end = _dt.date.fromisoformat(earning_year_end.strip())
        except ValueError:
            pass

    transition = db.query(EmploymentTransition).filter(EmploymentTransition.user_id == user_id).first()
    if transition is None:
        transition = EmploymentTransition(user_id=user_id)
        db.add(transition)

    transition.transition_date = t_date
    transition.consultant_salary_type = salary_type
    transition.consultant_vacation_days = parsed_vacation_days
    transition.consultant_supplement_pct = consultant_supplement_pct
    transition.variable_avg_daily_override = variable_override
    transition.earning_year_start = earning_start
    transition.earning_year_end = earning_end
    transition.notes = notes.strip() or None
    transition.updated_at = _dt.datetime.utcnow()

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Sätt ny direktlön från övergångsdatum
    if new_direct_salary.strip():
        try:
            salary_int = int(new_direct_salary.strip())
            from app.core.schedule import add_new_wage, clear_schedule_cache
            from app.database.database import WageHistory

            existing_wage = (
                db.query(WageHistory)
                .filter(
                    WageHistory.user_id == user_id,
                    WageHistory.effective_from == t_date,
                )
                .first()
            )
            if existing_wage:
                existing_wage.wage = salary_int
                db.commit()
            else:
                add_new_wage(
                    session=db,
                    user_id=user_id,
                    new_wage=salary_int,
                    effective_from=t_date,
                    created_by=current_user.id,
                )
            clear_schedule_cache()
        except (ValueError, Exception):
            pass

    # Återgå till standardsatser (OB/OT/beredskap) från övergångsdatum
    if reset_rates_to_default.strip():
        from app.core.rates import add_new_rates
        from app.core.schedule import clear_schedule_cache

        add_new_rates(
            session=db,
            user_id=user_id,
            rates={},
            effective_from=t_date,
            created_by=current_user.id,
        )
        clear_schedule_cache()

    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)


@router.post("/admin/users/{user_id}/transition/delete", name="admin_transition_delete")
async def admin_transition_delete(
    user_id: int,
    cleanup_wage: str = Form(""),
    cleanup_rates: str = Form(""),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: ta bort anstallningsorvergång for en anvandare."""
    from app.core.schedule import clear_schedule_cache
    from app.database.database import EmploymentTransition, RateHistory, WageHistory

    transition = db.query(EmploymentTransition).filter(EmploymentTransition.user_id == user_id).first()
    if transition:
        t_date = transition.transition_date
        if cleanup_wage.strip():
            db.query(WageHistory).filter(
                WageHistory.user_id == user_id,
                WageHistory.effective_from == t_date,
            ).delete()
        if cleanup_rates.strip():
            db.query(RateHistory).filter(
                RateHistory.user_id == user_id,
                RateHistory.effective_from == t_date,
            ).delete()
        db.delete(transition)
        try:
            db.commit()
            clear_schedule_cache()
        except Exception:
            db.rollback()
            raise

    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)


def _parse_rates_form(form) -> dict:
    """Parse rate form fields into custom_rates dict."""
    from app.core.rates import DEFAULT_OB_DIVISORS, DEFAULT_VACATION_RATES

    custom = {}

    # OB rates (kr/tim, fixed)
    ob = {}
    for code in DEFAULT_OB_DIVISORS:
        val = form.get(f"rate_ob_{code}", "").strip()
        if val:
            ob[code] = float(val)
    if ob:
        custom["ob"] = ob

    # OT rate (kr/tim, fixed)
    ot_val = form.get("rate_ot", "").strip()
    if ot_val:
        custom["ot"] = float(ot_val)

    # On-call rates (fixed SEK/hr) — UI shows 4 groups, fan out weekend to sub-codes
    oncall = {}
    for code in ["OC_WEEKDAY", "OC_WEEKEND", "OC_HOLIDAY", "OC_SPECIAL"]:
        val = form.get(f"rate_oc_{code}", "").strip()
        if val:
            rate = float(val)
            if code == "OC_WEEKEND":
                for sub in ["OC_WEEKEND", "OC_WEEKEND_SAT", "OC_WEEKEND_SUN", "OC_WEEKEND_MON", "OC_HOLIDAY_EVE"]:
                    oncall[sub] = rate
            else:
                oncall[code] = rate
    if oncall:
        custom["oncall"] = oncall

    # Vacation percentages
    vac = {}
    for key in DEFAULT_VACATION_RATES:
        val = form.get(f"rate_vac_{key}", "").strip()
        if val:
            vac[key] = float(val)
    if vac:
        custom["vacation"] = vac

    return custom


@router.post("/admin/users/{user_id}/update-rates", name="admin_update_rates")
async def admin_update_rates(
    request: Request,
    user_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: add new rate entry with effective date for a user."""
    from app.core.rates import add_new_rates

    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")

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
        user_id=user_id,
        rates=rates,
        effective_from=effective_date,
        created_by=current_user.id,
    )
    clear_schedule_cache()

    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)


@router.post("/admin/users/{user_id}/add-wage", name="admin_add_wage")
async def admin_add_wage(
    request: Request,
    user_id: int,
    new_wage: int = Form(...),
    effective_from: str = Form(...),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: add a new wage with effective date."""
    from datetime import datetime

    from app.core.schedule import add_new_wage

    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        # Parse effective_from date
        effective_date = datetime.strptime(effective_from, "%Y-%m-%d").date()

        # Add new wage
        add_new_wage(
            session=db,
            user_id=user_id,
            new_wage=new_wage,
            effective_from=effective_date,
            created_by=current_user.id,
        )

        # Clear schedule cache to update calculations
        clear_schedule_cache()

    except ValueError as e:
        # Invalid date format
        raise HTTPException(status_code=400, detail=f"Ogiltigt datum: {e}") from e
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Kunde inte lägga till lön: {e}") from e

    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)


@router.post("/admin/users/{user_id}/delete-wage/{wage_id}", name="admin_delete_wage")
async def admin_delete_wage(
    request: Request,
    user_id: int,
    wage_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: delete a wage history entry for any user."""
    from app.database.database import WageHistory

    # Get the wage record
    wage_record = db.query(WageHistory).filter(WageHistory.id == wage_id).first()

    if not wage_record:
        raise HTTPException(status_code=404, detail="Wage record not found")

    # Security check: ensure wage belongs to the user_id in the URL
    if wage_record.user_id != user_id:
        raise HTTPException(status_code=400, detail="Wage record does not belong to this user")

    # Check if this is the only wage record for this user
    total_wages = db.query(WageHistory).filter(WageHistory.user_id == user_id).count()

    if total_wages <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the only wage record")

    # If this was the current wage (effective_to is NULL), we need to reopen the previous wage
    if wage_record.effective_to is None:
        # Find the previous wage
        previous_wage = (
            db.query(WageHistory)
            .filter(
                WageHistory.user_id == user_id,
                WageHistory.id != wage_id,
                WageHistory.effective_to.isnot(None),
            )
            .order_by(WageHistory.effective_from.desc())
            .first()
        )

        if previous_wage:
            # Reopen the previous wage
            previous_wage.effective_to = None

            # Update User.wage to the previous wage
            edit_user = db.query(User).filter(User.id == user_id).first()
            if edit_user:
                edit_user.wage = previous_wage.wage

    # Delete the wage record
    db.delete(wage_record)
    db.commit()

    # Clear schedule cache
    clear_schedule_cache()

    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)


@router.post("/admin/users/{user_id}/delete-rate/{rate_id}", name="admin_delete_rate")
async def admin_delete_rate(
    user_id: int,
    rate_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: delete a rate history entry for a user."""
    from app.core.rates import delete_rate_history

    delete_rate_history(db, rate_id, user_id)
    clear_schedule_cache()

    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)


@router.post("/admin/users/{user_id}/end-employment", name="admin_end_employment")
async def admin_end_employment(
    request: Request,
    user_id: int,
    person_id: int = Form(...),
    end_date: str = Form(...),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: end a person's employment."""
    from datetime import datetime

    from app.core.schedule.person_history import end_employment

    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        # Parse end_date
        end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()

        # End employment
        end_employment(
            session=db,
            user_id=user_id,
            person_id=person_id,
            end_date=end_date_obj,
        )

        return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)

    except ValueError as e:
        # Invalid date format
        return templates.TemplateResponse(
            "admin_user_edit.html",
            {
                "request": request,
                "user": current_user,
                "edit_user": edit_user,
                "error": f"Ogiltigt datumformat: {e}",
            },
            status_code=400,
        )


@router.post("/admin/users/{user_id}/start-employment", name="admin_start_employment")
async def admin_start_employment(
    request: Request,
    user_id: int,
    person_id: int = Form(...),
    start_date: str = Form(...),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: start a person's employment at a position."""
    from datetime import datetime

    from app.core.schedule.person_history import start_employment

    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        # Parse start_date
        start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()

        # Start employment
        start_employment(
            session=db,
            user_id=user_id,
            person_id=person_id,
            name=edit_user.name,
            username=edit_user.username,
            start_date=start_date_obj,
            created_by=current_user.id,
        )

        return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)

    except ValueError as e:
        # Invalid date format
        return templates.TemplateResponse(
            "admin_user_edit.html",
            {
                "request": request,
                "user": current_user,
                "edit_user": edit_user,
                "error": f"Ogiltigt datumformat: {e}",
            },
            status_code=400,
        )


@router.post("/admin/users/{user_id}/delete-employment/{history_id}", name="admin_delete_employment")
async def admin_delete_employment(
    request: Request,
    user_id: int,
    history_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: delete a person history entry."""
    from app.database.database import PersonHistory

    # Get the history record
    history_record = db.query(PersonHistory).filter(PersonHistory.id == history_id).first()

    if not history_record:
        raise HTTPException(status_code=404, detail="Employment record not found")

    # Security check: ensure record belongs to the user_id in the URL
    if history_record.user_id != user_id:
        raise HTTPException(status_code=400, detail="Employment record does not belong to this user")

    person_id = history_record.person_id

    # If this was the current employment (effective_to is NULL), we need to handle it
    if history_record.effective_to is None:
        # Find the previous employment for this position
        previous_record = (
            db.query(PersonHistory)
            .filter(
                PersonHistory.person_id == person_id,
                PersonHistory.id != history_id,
                PersonHistory.effective_to.isnot(None),
            )
            .order_by(PersonHistory.effective_from.desc())
            .first()
        )

        if previous_record:
            # Reopen the previous record
            previous_record.effective_to = None

        # Clear the user's person_id since they no longer hold this position
        edit_user = db.query(User).filter(User.id == user_id).first()
        if edit_user and edit_user.person_id == person_id:
            edit_user.person_id = None

    # Check if this is the user's only employment record
    remaining_records = (
        db.query(PersonHistory).filter(PersonHistory.user_id == user_id, PersonHistory.id != history_id).count()
    )

    # If no remaining records, set user as inactive
    if remaining_records == 0:
        edit_user = db.query(User).filter(User.id == user_id).first()
        if edit_user:
            edit_user.is_active = 0
            edit_user.person_id = None

    # Delete the history record
    db.delete(history_record)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    clear_schedule_cache()

    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)


@router.post("/admin/users/{user_id}/reset-password", name="admin_reset_password")
async def admin_reset_password(
    request: Request,
    user_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: reset user password to default (London1) and force password change."""
    reset_user = db.query(User).filter(User.id == user_id).first()
    if not reset_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Reset password to London1
    default_password = DEFAULT_PASSWORD
    reset_user.password_hash = get_password_hash(default_password)
    reset_user.must_change_password = 1
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    clear_schedule_cache()

    # Log password reset
    log_auth_event(
        event_type="password_reset",
        username=reset_user.username,
        user_id=reset_user.id,
        success=True,
        details={"reset_by": current_user.username, "reset_by_id": current_user.id},
    )

    # Redirect with success message
    from urllib.parse import quote

    success_msg = f"Lösenordet för {reset_user.name} har återställts till {default_password}"
    return RedirectResponse(url=f"/admin/users?success={quote(success_msg)}", status_code=302)
