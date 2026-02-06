# app/routes/schedule_api.py
"""
API endpoints for schedule data - used for AJAX/lazy loading.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user_optional
from app.core.helpers import can_see_salary
from app.core.schedule import summarize_year_for_person
from app.core.validators import validate_person_id
from app.database.database import User, get_db

router = APIRouter(prefix="/api", tags=["schedule_api"])


@router.get("/year/{year}/totals/{person_id}")
async def get_year_totals(
    year: int,
    person_id: int,
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """API endpoint to get year OB totals for a specific person (for lazy loading)."""
    if current_user is None:
        return {"error": "Not authenticated"}, 401

    person_id = validate_person_id(person_id)

    # Check if user can see salary for this person
    if not can_see_salary(current_user, person_id):
        return {"total_ob": None}

    # Calculate year summary for this person
    year_summary = summarize_year_for_person(year, person_id, session=db, current_user=current_user)
    total_ob = year_summary["year_summary"].get("total_ob", 0.0)

    return {"person_id": person_id, "total_ob": total_ob, "year": year}
