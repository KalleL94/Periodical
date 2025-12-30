# app/core/helpers.py
"""
Shared helper functions for templates and route handlers.
"""

from fastapi import Request
from fastapi.templating import Jinja2Templates

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
    except Exception:
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
