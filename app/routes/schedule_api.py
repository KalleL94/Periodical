# app/routes/schedule_api.py
"""
API endpoints for schedule data - used for AJAX/lazy loading.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user_optional
from app.core.helpers import can_see_salary
from app.core.schedule import summarize_year_for_person
from app.core.validators import validate_person_id
from app.database.database import User, UserRole, get_db

router = APIRouter(prefix="/api", tags=["schedule_api"])


@router.get("/year/{year}/totals/{person_id}")
async def get_year_totals(
    year: int,
    person_id: int,
    user_id: int | None = None,
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """API endpoint to get year OB totals for a specific person (for lazy loading).

    When ``user_id`` is supplied the totals are scoped to that user's wage and
    employment period at ``person_id`` (used by the team year view where each
    holder of a position gets their own column). Without it the endpoint returns
    the unchanged position totals for backward compatibility.
    """
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    person_id = validate_person_id(person_id)

    # Check if user can see salary for this person
    if not can_see_salary(current_user, person_id):
        return {"total_ob": None}

    if user_id is not None:
        # Validate that the requested holder exists before scoping to them.
        if db.query(User).filter(User.id == user_id).first() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        # Non-admin callers may only request totals for a legitimate holder of
        # this position; otherwise they could read out any user's wage level via
        # the OB totals. Admins may scope to anyone. A holder is someone with a
        # PersonHistory record at person_id, or the legacy identity user_id ==
        # person_id.
        if current_user.role != UserRole.ADMIN:
            from app.database.database import PersonHistory

            is_history_holder = (
                db.query(PersonHistory)
                .filter(PersonHistory.user_id == user_id, PersonHistory.person_id == person_id)
                .first()
                is not None
            )
            if not is_history_holder and user_id != person_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized for this position",
                )
        year_summary = summarize_year_for_person(
            year,
            person_id,
            session=db,
            current_user=current_user,
            wage_user_id=user_id,
            employment_user_id=user_id,
        )
    else:
        year_summary = summarize_year_for_person(year, person_id, session=db, current_user=current_user)

    total_ob = year_summary["year_summary"].get("total_ob", 0.0)

    return {"person_id": person_id, "total_ob": total_ob, "year": year}
