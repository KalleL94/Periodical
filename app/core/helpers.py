# app/core/helpers.py
"""
Shared helper functions for templates and route handlers.
"""

from datetime import date

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.utils import get_today
from app.database.database import User, UserRole


def contrast_color(hex_color: str) -> str:
    """
    Return '#000' for light backgrounds, '#fff' for dark backgrounds.
    Used as a Jinja2 filter for badge text color.
    """
    if not hex_color:
        return "#fff"
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join([c * 2 for c in h])
    try:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
    except ValueError:
        return "#fff"
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#000" if lum > 0.5 else "#fff"


def can_see_salary(current_user: User | None, target_person_id: int) -> bool:
    """
    Check if current user can see salary data for target person.

    Rules:
    - Not logged in: No access
    - Admin: Full access to all
    - Regular user: Only own data
    """
    if current_user is None:
        return False
    if current_user.role == UserRole.ADMIN:
        return True
    return current_user.id == target_person_id


def can_see_data_for_date(
    current_user: User | None,
    target_person_id: int,
    target_date: date,
    session: Session,
) -> bool:
    """
    Check if user can see data for a specific person on a specific date.

    This function considers employment periods tracked in PersonHistory:
    - Admin: Always yes
    - Regular user: Only if they held that person_id on that date

    Args:
        current_user: Current logged-in user (or None)
        target_person_id: Position being viewed (1-10)
        target_date: Date of the data being viewed
        session: Database session for PersonHistory queries

    Returns:
        True if user can see the data, False otherwise

    Example:
        >>> # Kalle (user_id=6) held person_id=6 from 2026-01-02 to 2026-03-31
        >>> can_see_data_for_date(kalle_user, 6, date(2026, 2, 15), db)  # True
        >>> can_see_data_for_date(kalle_user, 6, date(2026, 5, 1), db)   # False (after employment ended)
    """
    if current_user is None:
        return False

    if current_user.role == UserRole.ADMIN:
        return True

    # Check if current_user held target_person_id on target_date
    from app.core.schedule.person_history import get_person_for_date

    person_data = get_person_for_date(session, target_person_id, target_date)

    if person_data:
        return person_data["user_id"] == current_user.id

    return False


def strip_salary_data(data: dict) -> dict:
    """
    Remove sensitive salary data from a summary dictionary.
    Used when user doesn't have permission to see salary info.
    """
    result = data.copy()
    result["brutto_pay"] = None
    result["netto_pay"] = None
    result["ob_pay"] = {}
    result["ob_hours"] = {}
    result["total_ob"] = None

    if "days" in result and result["days"]:
        stripped_days = []
        for day in result["days"]:
            day_copy = day.copy()
            day_copy["ob_pay"] = {}
            day_copy["ob_hours"] = {}
            stripped_days.append(day_copy)
        result["days"] = stripped_days

    return result


def strip_year_summary(summary: dict) -> dict:
    """
    Remove sensitive salary data from year summary.
    """
    result = summary.copy()
    result["total_netto"] = None
    result["total_brutto"] = None
    result["total_ob"] = None
    result["avg_netto"] = None
    result["avg_brutto"] = None
    result["avg_ob"] = None
    result["ob_hours_by_code"] = {}
    result["ob_pay_by_code"] = {}
    result["total_ob_hours"] = None
    return result


def render_template(
    templates: Jinja2Templates,
    template_name: str,
    request: Request,
    context: dict,
    user: User | None = None,
):
    """
    Render template with user context automatically included.
    """
    ctx = {"request": request, "user": user, "now": get_today()}
    ctx.update(context)
    return templates.TemplateResponse(template_name, ctx)
