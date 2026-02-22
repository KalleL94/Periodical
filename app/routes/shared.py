# app/routes/shared.py
"""
Shared utilities and templates for route modules.
"""

from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.constants import MAX_PERSONS, PERSON_IDS
from app.core.helpers import contrast_color
from app.core.utils import get_today

# Shared Jinja2 templates instance
templates = Jinja2Templates(directory="app/templates")

# Register Jinja filter for contrast color on badges
templates.env.filters["contrast"] = contrast_color

# Date/time filters – use {{ date | date_format }} and {{ time | time_format }} in templates
templates.env.filters["date_format"] = lambda v: v.strftime("%Y-%m-%d") if v else ""
templates.env.filters["time_format"] = lambda v: v.strftime("%H:%M") if v else ""

# Add now (today's date) as a global for templates
# Note: This is set once at module load. For dynamic "today", use get_today() in routes.
templates.env.globals["now"] = get_today()

# Expose person/rotation constants so templates don't need to hardcode range(1, 11)
templates.env.globals["person_ids"] = PERSON_IDS
templates.env.globals["max_persons"] = MAX_PERSONS


def redirect_if_not_own_data(current_user, user_id: int, redirect_url: str) -> RedirectResponse | None:
    """Return a redirect response if a non-admin user tries to view another user's data."""
    from app.database.database import UserRole

    if current_user.role != UserRole.ADMIN and current_user.id != user_id:
        return RedirectResponse(url=redirect_url, status_code=302)
    return None
