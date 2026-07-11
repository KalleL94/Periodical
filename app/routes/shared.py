# app/routes/shared.py
"""
Shared utilities and templates for route modules.
"""

import json as _json

from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.core.constants import MAX_PERSONS, PERSON_IDS
from app.core.helpers import contrast_color
from app.core.translations import TRANSLATIONS
from app.core.utils import get_today

# Shared Jinja2 templates instance
templates = Jinja2Templates(directory="app/templates")

# Register Jinja filter for contrast color on badges
templates.env.filters["contrast"] = contrast_color

# Date/time filters – use {{ date | date_format }} and {{ time | time_format }} in templates
templates.env.filters["date_format"] = lambda v: v.strftime("%Y-%m-%d") if v else ""
templates.env.filters["time_format"] = lambda v: v.strftime("%H:%M") if v else ""

# JSON parse filter – use {{ t.some_json_key | from_json }} in templates
templates.env.filters["from_json"] = _json.loads

# Add now (today's date) as a global for templates
# Note: This is set once at module load. For dynamic "today", use get_today() in routes.
templates.env.globals["now"] = get_today()

# Expose person/rotation constants so templates don't need to hardcode range(1, 11)
templates.env.globals["person_ids"] = PERSON_IDS
templates.env.globals["max_persons"] = MAX_PERSONS


def render(template_name: str, context: dict, status_code: int = 200, headers: dict | None = None):
    """Render a template, injecting the correct translation dict based on user.language."""
    user = context.get("user")
    lang = "sv"
    if user and hasattr(user, "language") and user.language:
        lang = user.language
    context["t"] = TRANSLATIONS.get(lang, TRANSLATIONS["sv"])
    request = context.get("request")
    return templates.TemplateResponse(request, template_name, context, status_code=status_code, headers=headers)


def redirect_if_not_own_data(current_user, user_id: int, redirect_url: str) -> RedirectResponse | None:
    """Return a redirect response if a non-admin user tries to view another user's data."""
    from app.database.database import UserRole

    if current_user.role != UserRole.ADMIN and current_user.id != user_id:
        return RedirectResponse(url=redirect_url, status_code=302)
    return None


def _resolve_person_param(db, raw_id: int, on_date=None):
    """Resolve a personal-view path parameter to (target_user, rotation_position).

    Personal views are keyed by USER id: whenever a User row with the given id
    exists, the parameter identifies that user and the rotation position comes
    from their PersonHistory (get_user_person_id). Only when no such user exists
    does the legacy rotation-position interpretation apply, validated via
    validate_person_id so out-of-range ids still raise 404.

    on_date selects which position a future-dated change resolves to: pass the
    viewed period's start date so the view resolves the position held during that
    period. Defaults to today (get_user_person_id resolves get_today()).

    Returns a tuple (target_user, rotation_position). target_user is the User
    when resolved as a user, otherwise None.
    """
    from app.core.schedule.person_history import get_user_person_id
    from app.core.validators import validate_person_id
    from app.database.database import User

    target_user = db.query(User).filter(User.id == raw_id).first()
    if target_user is not None:
        rotation_position = get_user_person_id(db, raw_id, on_date=on_date)
        if rotation_position is None:
            # No PersonHistory for a user id > 10: fall back to the User row's
            # rotation position (mirrors the previous >10 branch behaviour).
            rotation_position = target_user.rotation_person_id
        return target_user, rotation_position
    return None, validate_person_id(raw_id)


def build_position_nav(db) -> list[dict]:
    """Build the admin jump-bar entries: one per rotation position (1-10).

    Each entry links a position to its current holder's user-keyed page. An open
    PersonHistory record is the current holder; a position with only closed
    history is vacant (no link); positions without any history fall back to the
    position id so legacy setups keep working. Returns dicts with person_id,
    holder_user_id and vacant.
    """
    from app.core.schedule.person_history import get_current_person_for_position, has_position_history
    from app.database.database import PersonHistory

    nav = []
    for pid in PERSON_IDS:
        open_record = (
            db.query(PersonHistory).filter(PersonHistory.person_id == pid, PersonHistory.effective_to.is_(None)).first()
        )
        if open_record:
            nav.append({"person_id": pid, "holder_user_id": open_record.user_id, "vacant": False})
        elif has_position_history(db, pid):
            nav.append({"person_id": pid, "holder_user_id": None, "vacant": True})
        else:
            cp = get_current_person_for_position(db, pid)
            nav.append({"person_id": pid, "holder_user_id": cp["user_id"] if cp else pid, "vacant": False})
    return nav


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

    # Sick OB compensation flag
    custom["sick"] = {"ob_compensation": form.get("sick_ob_compensation") == "on"}

    return custom
