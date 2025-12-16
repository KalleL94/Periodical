import json
import tempfile
import shutil
from pathlib import Path
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.schedule import settings, persons, tax_brackets
from app.database.database import User, get_db
from app.auth.auth import get_admin_user

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

# Add now (today's date) as a global for templates
from datetime import date
templates.env.globals["now"] = date.today()


def write_json_safely(file_path: Path, data: dict | list) -> None:
    """
    Safely write JSON to a file using atomic write pattern.
    Writes to a temp file first, then replaces the original.
    """
    # Write to temp file in the same directory
    with tempfile.NamedTemporaryFile(
        mode='w',
        encoding='utf-8',
        dir=file_path.parent,
        delete=False,
        suffix='.tmp'
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
        persons_with_db_wages.append({
            "id": user.id,
            "name": user.name,
            "wage": user.wage
        })
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
    # Update settings.json (for default monthly_salary only)
    settings_path = Path("data/settings.json")
    settings_data = json.loads(settings_path.read_text(encoding="utf-8"))
    settings_data["monthly_salary"] = monthly_salary
    write_json_safely(settings_path, settings_data)

    # Parse wage updates from form
    wage_updates = json.loads(person_wages)

    # Update wages in database (single source of truth)
    for person_id_str, new_wage in wage_updates.items():
        person_id = int(person_id_str)
        user = db.query(User).filter(User.id == person_id).first()
        if user:
            user.wage = int(new_wage)
    
    db.commit()
    
    # Clear schedule cache to ensure new wages are used
    clear_schedule_cache()

    # Redirect back to GET (Post-Redirect-Get pattern)
    return RedirectResponse(url="/admin/settings", status_code=303)
