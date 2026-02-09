import datetime
import json
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth.auth import get_admin_user
from app.core.schedule import clear_schedule_cache, settings, tax_brackets
from app.core.schedule.vacation import calculate_vacation_balance
from app.core.utils import get_today
from app.database.database import Absence, AbsenceType, RotationEra, User, get_db

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

# Add now (today's date) as a global for templates

templates.env.globals["now"] = get_today()


def write_json_safely(file_path: Path, data: dict | list) -> None:
    """
    Safely write JSON to a file using atomic write pattern.
    Writes to a temp file first, then replaces the original.
    """
    # Write to temp file in the same directory
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=file_path.parent, delete=False, suffix=".tmp"
    ) as tmp_file:
        json.dump(data, tmp_file, indent=4, ensure_ascii=False)
        tmp_path = tmp_file.name

    # Replace original file atomically
    shutil.move(tmp_path, file_path)


@router.get("/settings", response_class=HTMLResponse, name="admin_settings")
async def admin_settings(
    request: Request,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    # Get all users with their database wages
    users = db.query(User).filter(User.id.in_(range(1, 11))).order_by(User.id).all()

    # Create persons-like list for template compatibility
    persons_with_db_wages = []
    for user in users:
        persons_with_db_wages.append({"id": user.id, "name": user.name, "wage": user.wage})
    return templates.TemplateResponse(
        "admin_settings.html",
        {
            "request": request,
            "user": current_user,
            "settings": settings,
            "persons": persons_with_db_wages,
            "tax_brackets": tax_brackets,
        },
    )


@router.post("/settings", name="admin_settings_update")
async def admin_settings_update(
    request: Request,
    monthly_salary: int = Form(...),
    person_wages: str = Form(...),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """
    Update settings and person wages.
    Form data:
    - monthly_salary: int
    - person_wages: JSON string like {"1": 37000, "2": 37000, ...}
    """
    # Validate monthly_salary range (from previous fix)
    if not (1000 <= monthly_salary <= 1000000):
        return templates.TemplateResponse(
            "admin_settings.html",
            {
                "request": request,
                "user": current_user,
                "settings": settings,
                "persons": [],  # Placeholder
                "tax_brackets": tax_brackets,
                "error": "Ogiltig månads lön: måste vara mellan 1000 och 1000000",
            },
            status_code=400,
        )

    # Parse wage updates from form with error handling
    try:
        wage_updates = json.loads(person_wages)
    except json.JSONDecodeError:
        return templates.TemplateResponse(
            "admin_settings.html",
            {
                "request": request,
                "user": current_user,
                "settings": settings,
                "persons": [],  # Placeholder
                "tax_brackets": tax_brackets,
                "error": "Ogiltig JSON för löner: kontrollera formatet",
            },
            status_code=400,
        )

    # Validate each wage in wage_updates (from previous fix)
    for person_id_str, new_wage in wage_updates.items():
        if not (1000 <= int(new_wage) <= 1000000):
            return templates.TemplateResponse(
                "admin_settings.html",
                {
                    "request": request,
                    "user": current_user,
                    "settings": settings,
                    "persons": [],  # Placeholder
                    "tax_brackets": tax_brackets,
                    "error": f"Ogiltig lön för person {person_id_str}: måste vara mellan 1000 och 1000000",
                },
                status_code=400,
            )

    # Update settings.json (for default monthly_salary only)
    settings_path = Path("data/settings.json")
    try:
        settings_data = json.loads(settings_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return templates.TemplateResponse(
            "admin_settings.html",
            {
                "request": request,
                "user": current_user,
                "settings": settings,
                "persons": [],
                "tax_brackets": tax_brackets,
                "error": "Konfigurationsfil saknas. Kontakta administratör.",
            },
            status_code=500,
        )
    except json.JSONDecodeError as e:
        return templates.TemplateResponse(
            "admin_settings.html",
            {
                "request": request,
                "user": current_user,
                "settings": settings,
                "persons": [],
                "tax_brackets": tax_brackets,
                "error": f"Korrupt konfigurationsfil: {e}. Kontakta administratör.",
            },
            status_code=500,
        )
    except OSError as e:
        return templates.TemplateResponse(
            "admin_settings.html",
            {
                "request": request,
                "user": current_user,
                "settings": settings,
                "persons": [],
                "tax_brackets": tax_brackets,
                "error": f"Kunde inte läsa konfigurationsfil: {e}. Kontakta administratör.",
            },
            status_code=500,
        )

    settings_data["monthly_salary"] = monthly_salary
    write_json_safely(settings_path, settings_data)

    # Update wages in database (single source of truth)
    for person_id_str, new_wage in wage_updates.items():
        person_id = int(person_id_str)
        user = db.query(User).filter(User.id == person_id).first()
        if user:
            user.wage = int(new_wage)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Clear schedule cache to ensure new wages are used
    clear_schedule_cache()

    # Redirect back to GET (Post-Redirect-Get pattern)
    return RedirectResponse(url="/admin/settings", status_code=303)


@router.get("/rotation-eras", response_class=HTMLResponse, name="admin_rotation_eras")
async def admin_rotation_eras(
    request: Request,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """View and manage rotation eras."""
    # Get all eras ordered by start_date (most recent first)
    eras = db.query(RotationEra).order_by(RotationEra.start_date.desc()).all()

    return templates.TemplateResponse(
        "admin_rotation_eras.html",
        {
            "request": request,
            "user": current_user,
            "eras": eras,
        },
    )


@router.post("/rotation-eras/create", name="admin_rotation_eras_create")
async def admin_rotation_eras_create(
    request: Request,
    start_date: str = Form(...),
    rotation_length: int = Form(...),
    weeks_pattern: str = Form(...),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Create a new rotation era."""
    from datetime import datetime

    try:
        # Parse and validate start_date
        new_start_date = datetime.strptime(start_date, "%Y-%m-%d").date()

        # Validate rotation_length
        if not (1 <= rotation_length <= 52):
            raise ValueError("Rotation length must be between 1 and 52 weeks")

        # Parse weeks_pattern (should be JSON)
        weeks_pattern_dict = json.loads(weeks_pattern)

        # Validate that weeks_pattern has the right number of weeks
        if len(weeks_pattern_dict) != rotation_length:
            raise ValueError(f"Weeks pattern must have exactly {rotation_length} weeks, got {len(weeks_pattern_dict)}")

        # Check for overlapping eras
        overlapping = (
            db.query(RotationEra)
            .filter(RotationEra.start_date <= new_start_date)
            .filter((RotationEra.end_date.is_(None)) | (RotationEra.end_date > new_start_date))
            .first()
        )

        if overlapping:
            # Close the overlapping era by setting its end_date
            overlapping.end_date = new_start_date
            db.add(overlapping)

        # Create new era
        new_era = RotationEra(
            start_date=new_start_date,
            end_date=None,  # New era is ongoing
            rotation_length=rotation_length,
            weeks_pattern=weeks_pattern_dict,
            created_by=current_user.id,
        )

        db.add(new_era)
        db.commit()

        # Clear cache since rotation configuration changed
        clear_schedule_cache()

        return RedirectResponse(url="/admin/rotation-eras", status_code=303)

    except (ValueError, json.JSONDecodeError) as e:
        db.rollback()
        # Re-fetch eras for error display
        eras = db.query(RotationEra).order_by(RotationEra.start_date.desc()).all()
        return templates.TemplateResponse(
            "admin_rotation_eras.html",
            {
                "request": request,
                "user": current_user,
                "eras": eras,
                "error": f"Error creating era: {str(e)}",
            },
            status_code=400,
        )


@router.post("/rotation-eras/delete/{era_id}", name="admin_rotation_eras_delete")
async def admin_rotation_eras_delete(
    era_id: int,
    request: Request,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Delete a rotation era."""
    try:
        # Find the era to delete
        era = db.query(RotationEra).filter(RotationEra.id == era_id).first()

        if not era:
            raise ValueError(f"Era with id {era_id} not found")

        # Check if this is the only era
        total_eras = db.query(RotationEra).count()
        if total_eras == 1:
            raise ValueError("Cannot delete the only rotation era. At least one era must exist.")

        # If deleting an ongoing era (end_date is NULL), reopen the previous era
        if era.end_date is None:
            previous_era = (
                db.query(RotationEra)
                .filter(RotationEra.end_date == era.start_date)
                .order_by(RotationEra.start_date.desc())
                .first()
            )

            if previous_era:
                previous_era.end_date = None  # Make it ongoing again
                db.add(previous_era)

        # Delete the era
        db.delete(era)
        db.commit()

        # Clear cache since rotation configuration changed
        clear_schedule_cache()

        return RedirectResponse(url="/admin/rotation-eras", status_code=303)

    except ValueError as e:
        db.rollback()
        # Re-fetch eras for error display
        eras = db.query(RotationEra).order_by(RotationEra.start_date.desc()).all()
        return templates.TemplateResponse(
            "admin_rotation_eras.html",
            {
                "request": request,
                "user": current_user,
                "eras": eras,
                "error": f"Error deleting era: {str(e)}",
            },
            status_code=400,
        )


# ---------------------------------------------------------------------------
# Admin Vacation Management
# ---------------------------------------------------------------------------

MONTH_NAMES_SV = [
    "Januari",
    "Februari",
    "Mars",
    "April",
    "Maj",
    "Juni",
    "Juli",
    "Augusti",
    "September",
    "Oktober",
    "November",
    "December",
]


@router.get("/vacation", response_class=HTMLResponse, name="admin_vacation")
async def admin_vacation(
    request: Request,
    year: int | None = None,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: team vacation overview with heatmap and balance table."""
    if year is None:
        year = get_today().year

    users = db.query(User).filter(User.is_active == 1, User.id != 0).order_by(User.person_id).all()

    team_data = []
    for u in users:
        vacation_weeks = (u.vacation or {}).get(str(year), [])
        balance = calculate_vacation_balance(u, year, db)

        # Get day-level vacation dates for this year
        day_absences = (
            db.query(Absence)
            .filter(
                Absence.user_id == u.id,
                Absence.absence_type == AbsenceType.VACATION,
                Absence.date >= datetime.date(year, 1, 1),
                Absence.date <= datetime.date(year, 12, 31),
            )
            .all()
        )
        day_vacation_weeks = set()
        for a in day_absences:
            day_vacation_weeks.add(a.date.isocalendar()[1])

        team_data.append(
            {
                "user": u,
                "vacation_weeks": sorted(vacation_weeks),
                "day_vacation_weeks": sorted(day_vacation_weeks),
                "balance": balance,
            }
        )

    return templates.TemplateResponse(
        "admin_vacation.html",
        {
            "request": request,
            "user": current_user,
            "year": year,
            "team_data": team_data,
            "weeks_range": range(1, 53),
        },
    )


@router.get("/vacation/{user_id}", response_class=HTMLResponse, name="admin_vacation_user")
async def admin_vacation_user(
    request: Request,
    user_id: int,
    year: int | None = None,
    success: str | None = Query(None),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: edit vacation for a specific user."""
    if year is None:
        year = get_today().year

    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        return RedirectResponse(url="/admin/vacation", status_code=302)

    vacation_weeks = (edit_user.vacation or {}).get(str(year), [])
    balance = calculate_vacation_balance(edit_user, year, db)

    # Get day-level vacation for this year
    day_absences = (
        db.query(Absence)
        .filter(
            Absence.user_id == user_id,
            Absence.absence_type == AbsenceType.VACATION,
            Absence.date >= datetime.date(year, 1, 1),
            Absence.date <= datetime.date(year, 12, 31),
        )
        .order_by(Absence.date)
        .all()
    )

    return templates.TemplateResponse(
        "admin_vacation_user.html",
        {
            "request": request,
            "user": current_user,
            "edit_user": edit_user,
            "year": year,
            "vacation_weeks": sorted(vacation_weeks),
            "balance": balance,
            "day_absences": day_absences,
            "success": success,
            "month_names": MONTH_NAMES_SV,
        },
    )


@router.post("/vacation/{user_id}/weeks", name="admin_update_vacation_weeks")
async def admin_update_vacation_weeks(
    user_id: int,
    year: int = Form(...),
    weeks: str = Form(""),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: update week-based vacation for a user."""
    from sqlalchemy.orm.attributes import flag_modified

    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        return RedirectResponse(url="/admin/vacation", status_code=302)

    # Validate year
    if not (2020 <= year <= 2100):
        return RedirectResponse(url=f"/admin/vacation/{user_id}?year={year}", status_code=302)

    # Parse and validate weeks
    week_list = []
    if weeks.strip():
        week_list = [int(w.strip()) for w in weeks.split(",") if w.strip().isdigit()]
        week_list = sorted(set(w for w in week_list if 1 <= w <= 53))

    # Update vacation JSON
    vacation = edit_user.vacation or {}
    vacation[str(year)] = week_list
    edit_user.vacation = vacation
    flag_modified(edit_user, "vacation")
    db.commit()

    clear_schedule_cache()

    return RedirectResponse(
        url=f"/admin/vacation/{user_id}?year={year}&success=Semesterveckor+uppdaterade",
        status_code=303,
    )


@router.post("/vacation/{user_id}/days", name="admin_add_vacation_day")
async def admin_add_vacation_day(
    user_id: int,
    vacation_date: str = Form(...),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: add a day-level vacation (VACATION absence)."""
    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        return RedirectResponse(url="/admin/vacation", status_code=302)

    try:
        d = datetime.date.fromisoformat(vacation_date)
    except ValueError:
        return RedirectResponse(url=f"/admin/vacation/{user_id}", status_code=302)

    # Check if absence already exists for this date
    existing = (
        db.query(Absence)
        .filter(
            Absence.user_id == user_id,
            Absence.date == d,
        )
        .first()
    )

    if existing:
        # Update existing absence to VACATION
        existing.absence_type = AbsenceType.VACATION
    else:
        new_absence = Absence(
            user_id=user_id,
            date=d,
            absence_type=AbsenceType.VACATION,
        )
        db.add(new_absence)

    db.commit()
    clear_schedule_cache()

    return RedirectResponse(
        url=f"/admin/vacation/{user_id}?year={d.year}&success=Semesterdag+tillagd",
        status_code=303,
    )


@router.post("/vacation/{user_id}/days/sync", name="admin_sync_vacation_days")
async def admin_sync_vacation_days(
    user_id: int,
    year: int = Form(...),
    dates: str = Form(""),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: sync day-level vacation for a year. Adds new, removes deselected."""
    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        return RedirectResponse(url="/admin/vacation", status_code=302)

    # Parse submitted dates
    new_dates: set[datetime.date] = set()
    for s in dates.split(","):
        s = s.strip()
        if s:
            try:
                new_dates.add(datetime.date.fromisoformat(s))
            except ValueError:
                continue

    # Get existing VACATION absences for this year
    existing = (
        db.query(Absence)
        .filter(
            Absence.user_id == user_id,
            Absence.absence_type == AbsenceType.VACATION,
            Absence.date >= datetime.date(year, 1, 1),
            Absence.date <= datetime.date(year, 12, 31),
        )
        .all()
    )
    existing_dates = {a.date: a for a in existing}

    # Add new dates
    for d in new_dates - set(existing_dates.keys()):
        db.add(Absence(user_id=user_id, date=d, absence_type=AbsenceType.VACATION))

    # Remove deselected dates
    for d in set(existing_dates.keys()) - new_dates:
        db.delete(existing_dates[d])

    db.commit()
    clear_schedule_cache()

    added = len(new_dates - set(existing_dates.keys()))
    removed = len(set(existing_dates.keys()) - new_dates)
    msg = f"{added}+tillagda,+{removed}+borttagna" if added or removed else "Inga+ändringar"

    return RedirectResponse(
        url=f"/admin/vacation/{user_id}?year={year}&success={msg}",
        status_code=303,
    )


@router.post(
    "/vacation/{user_id}/days/{absence_id}/delete",
    name="admin_delete_vacation_day",
)
async def admin_delete_vacation_day(
    user_id: int,
    absence_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: remove a day-level vacation."""
    absence = (
        db.query(Absence)
        .filter(
            Absence.id == absence_id,
            Absence.user_id == user_id,
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

    return RedirectResponse(
        url=f"/admin/vacation/{user_id}?year={year}&success=Semesterdag+borttagen",
        status_code=303,
    )


@router.post("/vacation/{user_id}/settings", name="admin_update_vacation_settings")
async def admin_update_vacation_settings(
    user_id: int,
    employment_start_date: str = Form(""),
    vacation_year_start_month: int = Form(4),
    vacation_days_per_year: int = Form(25),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: update vacation settings for a user."""
    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        return RedirectResponse(url="/admin/vacation", status_code=302)

    # Parse employment start date
    if employment_start_date.strip():
        try:
            edit_user.employment_start_date = datetime.date.fromisoformat(employment_start_date)
        except ValueError:
            pass
    else:
        edit_user.employment_start_date = None

    # Validate and set break month
    if 1 <= vacation_year_start_month <= 12:
        edit_user.vacation_year_start_month = vacation_year_start_month

    # Validate and set days per year
    if 0 <= vacation_days_per_year <= 40:
        edit_user.vacation_days_per_year = vacation_days_per_year

    db.commit()
    clear_schedule_cache()

    return RedirectResponse(
        url=f"/admin/vacation/{user_id}?success=Semesterinst%C3%A4llningar+sparade",
        status_code=303,
    )
