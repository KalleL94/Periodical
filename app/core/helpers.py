# app/core/helpers.py
"""
Shared helper functions for templates and route handlers.
"""

from datetime import date
from urllib.parse import quote, urlparse

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.database.database import User, UserRole


def require_own_or_admin(current_user: User, target_user_id: int, detail: str = "Not authorized") -> None:
    """Raise HTTP 403 if current_user is neither admin nor the target user."""
    if current_user.role != UserRole.ADMIN and target_user_id != current_user.id:
        raise HTTPException(status_code=403, detail=detail)


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


def can_see_salary(current_user: User | None, target_user_id: int | None) -> bool:
    """
    Check whether current_user may see the pay of the user with target_user_id.

    Pay belongs to a user, not to a rotation position: it is their wage, their rates,
    their employment period. Matching the viewer's current position against the viewed
    one gave the same answer right up until two users swapped positions, at which point
    each of them matched the other's history and stopped matching their own: the swap
    partner's pay was readable and their own was not.

    Rules:
    - Not logged in: no access
    - Admin: full access to all
    - Everyone else: their own pay only

    A position with no PersonHistory at all keeps the legacy identity
    user_id == person_id, so the callers that only have such a position pass its number
    here unchanged and resolve to the same user they always did.
    """
    if current_user is None or target_user_id is None:
        return False
    if current_user.role == UserRole.ADMIN:
        return True
    return current_user.id == target_user_id


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


# Every key below is an amount in kronor. Anything here is hidden from a viewer who
# may not see the target's pay; hour counts and day counts are not listed, because
# the schedule itself is shared and those are visible from it anyway.
#
# Keep this list closed over both summary shapes. The month shape and the year shape
# name the same figures differently (brutto_pay vs total_brutto), and when the two
# were stripped by two hand-maintained functions the year shape kept its totals and
# both shapes kept on-call pay, overtime pay and every deduction.
_MONEY_SCALARS = frozenset(
    {
        # Month shape
        "brutto_pay",
        "netto_pay",
        "tax",
        "tax_computed",
        "base_salary",
        "total_ob",
        "oncall_pay",
        "ot_pay",
        "absence_deduction",
        "vacation_supplement",
        "sick_ob_pay",
        "sick_total_ob",
        "sick_ob_lost",
        "transition_direct",
        # Year shape
        "total_netto",
        "total_brutto",
        "avg_netto",
        "avg_brutto",
        "avg_ob",
        "total_ob_hours",
        "total_oncall",
        "avg_oncall",
        "total_ot",
        "avg_ot",
        "total_absence_deduction",
        "avg_absence_deduction",
        "sick_deduction",
        "vab_deduction",
        "leave_deduction",
        "off_deduction",
        "total_vacation_supplement",
        "avg_vacation_supplement",
        "total_sick_ob_pay",
        "avg_sick_ob_pay",
        "total_sick_ob_lost",
        "total_sick_total_ob",
        "avg_sick_total_ob",
    }
)

# Mappings of code -> kronor (or -> hours that only appear beside kronor). Blanked to
# an empty dict so the templates' .get(code, 0) still works.
_MONEY_MAPS = frozenset(
    {
        "ob_pay",
        "ob_hours",
        "ob_pay_by_code",
        "ob_hours_by_code",
        # Manual payslip adjustments are amounts in kronor, like any other figure here.
        "override_deltas",
    }
)


def _strip_money(data: dict) -> dict:
    """Blank every pay figure in a month or year summary, leaving the schedule intact."""
    result = data.copy()
    for key in result.keys() & _MONEY_SCALARS:
        result[key] = None
    for key in result.keys() & _MONEY_MAPS:
        result[key] = {}
    if "tax_table" in result:
        result["tax_table"] = None
    return result


def strip_salary_data(data: dict) -> dict:
    """Remove pay data from a month summary, for a viewer without salary permission."""
    result = _strip_money(data)

    if result.get("days"):
        result["days"] = [day | {"ob_pay": {}, "ob_hours": {}} for day in result["days"]]

    return result


def strip_year_summary(summary: dict) -> dict:
    """Remove pay data from a year summary, for a viewer without salary permission."""
    return _strip_money(summary)


def is_safe_redirect(url: str) -> bool:
    """True when url is a local path, so it cannot become an open redirect."""
    if not url:
        return False
    parsed = urlparse(url)
    return not parsed.scheme and not parsed.netloc and url.startswith("/") and not url.startswith("//")


# ponytail: one insert per date, no batching. 62 days is two months, the widest a
# month view can select. Batch the writes if a real use case ever needs more.
MAX_EDIT_DATES = 62


def apply_to_dates(dates, write, conflicts):
    """Run write(date) for each date, skipping conflicts when more than one is given.

    A single-date post is a deliberate edit of one day: the caller can see what it
    replaces, so it upserts and the conflict check is never consulted. Ten dates at
    once is a different act, and silently overwriting nine days is the kind of error
    that surfaces a month later in a pay forecast.

    Returns (written dates, [(skipped date, reason)]).
    """
    if len(dates) > MAX_EDIT_DATES:
        raise HTTPException(status_code=400, detail=f"Too many dates, the limit is {MAX_EDIT_DATES}")

    written, skipped = [], []
    for day in dates:
        reason = conflicts(day) if len(dates) > 1 else None
        if reason:
            skipped.append((day, reason))
            continue
        write(day)
        written.append(day)
    return written, skipped


def result_param(written: list, skipped: list, verb: str = "satta") -> str:
    """The ?success= fragment describing a multi-date write. Empty for a single date.

    It reuses the success query parameter the admin pages already render with
    `alert alert--success`, so the skip report needs no markup of its own.
    """
    if not skipped and len(written) <= 1:
        return ""
    parts = [f"{len(written)} dagar {verb}"]
    if skipped:
        detail = ", ".join(f"{d.strftime('%d %b')} {reason}" for d, reason in skipped)
        parts.append(f"{len(skipped)} hoppades över: {detail}")
    return "?success=" + quote(". ".join(parts))


def edit_redirect_url(
    user_id: int, dates: list, return_to: str, written: list, skipped: list, verb: str = "satta"
) -> str:
    """Where an edit route sends the browser after writing.

    return_to when it is a safe relative path, otherwise the first date's day page.
    A multi-date write appends the result fragment so the landing page can report
    what was skipped.
    """
    fragment = result_param(written, skipped, verb)
    if not is_safe_redirect(return_to):
        first = dates[0]
        return f"/day/{user_id}/{first.year}/{first.month}/{first.day}" + fragment
    if fragment and "?" in return_to:
        fragment = "&" + fragment[1:]
    return return_to + fragment
