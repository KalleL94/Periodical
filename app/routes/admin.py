import json
import tempfile
import shutil
from pathlib import Path
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.schedule import settings, persons, tax_brackets

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


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
async def admin_settings(request: Request):
    return templates.TemplateResponse(
        "admin_settings.html",
        {
            "request": request,
            "settings": settings,
            "persons": persons,
            "tax_brackets": tax_brackets,
        },
    )


@router.post("/settings", name="admin_settings_update")
async def admin_settings_update(
    request: Request,
    monthly_salary: int = Form(...),
    person_wages: str = Form(...)  # JSON string of {person_id: wage}
):
    """
    Update settings and person wages.
    Form data:
    - monthly_salary: int
    - person_wages: JSON string like {"1": 37000, "2": 37000, ...}
    """
    # Update settings.json
    settings_path = Path("data/settings.json")
    settings_data = json.loads(settings_path.read_text(encoding="utf-8"))
    settings_data["monthly_salary"] = monthly_salary
    write_json_safely(settings_path, settings_data)

    # Update persons.json
    persons_path = Path("data/persons.json")
    persons_data = json.loads(persons_path.read_text(encoding="utf-8"))

    # Parse wage updates from form
    wage_updates = json.loads(person_wages)

    for person in persons_data:
        person_id = str(person["id"])
        if person_id in wage_updates:
            person["wage"] = int(wage_updates[person_id])

    write_json_safely(persons_path, persons_data)

    # Reload data in memory (force module reload)
    # Note: This is a simple approach. For production, consider using dependency injection
    # or a proper state management system
    import importlib
    import app.core.schedule as schedule_module
    importlib.reload(schedule_module)

    # Redirect back to GET (Post-Redirect-Get pattern)
    return RedirectResponse(url="/admin/settings", status_code=303)
