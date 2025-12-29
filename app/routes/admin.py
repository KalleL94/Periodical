import json
import shutil
import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth.auth import get_admin_user
from app.core.schedule import clear_schedule_cache, settings, tax_brackets
from app.database.database import RotationEra, User, get_db

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

# Add now (today's date) as a global for templates

templates.env.globals["now"] = date.today()


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
    settings_data = json.loads(settings_path.read_text(encoding="utf-8"))
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
