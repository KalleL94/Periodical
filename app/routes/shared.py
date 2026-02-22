# app/routes/shared.py
"""
Shared utilities and templates for route modules.
"""

from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

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


# ============ Pydantic schemas ============


class UserCreate(BaseModel):
    username: str
    password: str
    name: str
    wage: int


class UserUpdate(BaseModel):
    name: str | None = None
    wage: int | None = None
    vacation: dict | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


# ============ Shared form helpers ============


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
