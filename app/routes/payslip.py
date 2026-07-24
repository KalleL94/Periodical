# app/routes/payslip.py
"""Payslip view: the month's pay rows, manual overrides and upload comparison.

The rows themselves are built in app/core/schedule/payslip.py, from inside
summarize_month_for_person, so this page can never disagree with the month and
year views about the same month's pay.
"""

from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user_optional
from app.core.helpers import can_see_salary, require_own_or_admin
from app.core.logging_config import get_logger
from app.core.payslip_import import parse_payslip_pdf
from app.core.rates import get_user_rates
from app.core.schedule import build_calendar_grid_for_month, clear_schedule_cache, rotation_start_date
from app.core.schedule.payslip import ROW_ORDER, add_vacation_row, compare_to_upload
from app.core.schedule.vacation import calculate_vacation_balance
from app.core.utils import get_safe_today
from app.core.validators import validate_date_params
from app.database.database import PayslipOverride, User, UserRole, get_db
from app.routes.shared import _resolve_person_param, render

logger = get_logger(__name__)

router = APIRouter(tags=["payslip"])

# A payslip PDF is a couple of hundred kilobytes; anything larger is not one.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024


def _resolve_target(db: Session, person_id: int, year: int, month: int) -> tuple[User | None, int, int]:
    """Resolve the path parameter the way the month view does.

    Returns (target_user, rotation_position, wage_user_id).
    """
    target_user, rotation_position = _resolve_person_param(db, person_id, on_date=date(year, month, 1))
    wage_user_id = target_user.id if target_user is not None else person_id
    return target_user, rotation_position, wage_user_id


def _vacation_supplement(db: Session, user: User | None, year: int, days: int) -> float:
    """The month's vacation supplement, as the month view computes it.

    It is folded into gross outside summarize_month_for_person, so the payslip
    row is added here rather than inside the row builder.
    """
    if not days or user is None:
        return 0.0
    try:
        balance = calculate_vacation_balance(user, year, db)
        return round(balance.get("pay", {}).get("supplement_per_day", 0) * days, 0)
    except Exception:
        logger.warning("Semestertillägg kunde inte beräknas för user_id=%s", user.id, exc_info=True)
        return 0.0


def _build_context(request: Request, person_id: int, year: int, month: int, current_user: User | None, db: Session):
    """Shared page context for the payslip view. Raises 403 without salary access."""
    validate_date_params(year, month, None)

    target_user, rotation_position, wage_user_id = _resolve_target(db, person_id, year, month)

    if not can_see_salary(current_user, rotation_position):
        # A payslip is the most sensitive page in the app: never fall through.
        raise HTTPException(status_code=403, detail="Åtkomst nekad")

    employment_start = employment_end = None
    if target_user is not None:
        from app.core.schedule.person_history import get_employment_period

        employment_start, employment_end = get_employment_period(db, target_user.id, rotation_position)

    # build_calendar_grid_for_month rather than summarize_month_for_person
    # directly: it owns the employment masking and the mid-month position
    # stitching, so the payslip shows the same month the month view shows.
    summary = build_calendar_grid_for_month(
        year,
        month,
        rotation_position,
        session=db,
        employment_start=employment_start,
        employment_end=employment_end,
        viewer_user_id=target_user.id if target_user is not None else None,
        wage_user_id=wage_user_id,
    )["summary"]

    # A month entirely outside the viewer's own tenure has no payslip: the base
    # salary row would still print a full monthly wage the person was not paid.
    # The month view hides its summary for the same reason.
    import calendar as _cal

    month_first = date(year, month, 1)
    month_last = date(year, month, _cal.monthrange(year, month)[1])
    outside_employment = (employment_start is not None and employment_start > month_last) or (
        employment_end is not None and employment_end < month_first
    )

    slip = summary["payslip"]
    vacation_days = summary.get("vacation_days", 0)
    add_vacation_row(slip, _vacation_supplement(db, target_user, year, vacation_days), vacation_days)

    person_name = target_user.name if target_user is not None else summary.get("person_name")

    return {
        "request": request,
        "user": current_user,
        "person_id": person_id,
        "person_name": person_name,
        "year": year,
        "month": month,
        "slip": slip,
        "brutto_pay": summary.get("brutto_pay", 0.0),
        "can_edit": current_user is not None
        and (current_user.role == UserRole.ADMIN or current_user.id == wage_user_id),
        "wage_user": target_user,
        "outside_employment": outside_employment,
    }


@router.get("/month/{person_id}/payslip", response_class=HTMLResponse, name="payslip_person")
async def show_payslip(
    request: Request,
    person_id: int,
    year: int = None,
    month: int = None,
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """The month's payslip rows, with per-row override forms and the upload form."""
    if current_user is None:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)

    safe_today = get_safe_today(rotation_start_date)
    year = year or safe_today.year
    month = month or safe_today.month

    context = _build_context(request, person_id, year, month, current_user, db)
    if context["outside_employment"]:
        return RedirectResponse(url=f"/month/{person_id}?year={year}&month={month}", status_code=302)

    return render("payslip.html", context)


def _parse_amount(raw: str) -> float:
    """Parse a form amount, accepting the Swedish decimal comma."""
    try:
        return float(raw.replace(" ", "").replace(",", "."))
    except ValueError:
        raise HTTPException(status_code=400, detail="Ogiltigt belopp") from None


@router.post("/month/{person_id}/payslip/override", name="payslip_override")
async def set_payslip_override(
    person_id: int,
    year: int = Form(...),
    month: int = Form(...),
    row_key: str = Form(...),
    amount: str = Form(default=""),
    hours: str = Form(default=""),
    reason: str = Form(default=""),
    current_user: User = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Upsert (or, with an empty amount, delete) one manual payslip row."""
    if current_user is None:
        raise HTTPException(status_code=401, detail="Inloggning krävs")

    validate_date_params(year, month, None)
    if row_key not in ROW_ORDER:
        raise HTTPException(status_code=400, detail="Okänd lönerad")

    _, _, wage_user_id = _resolve_target(db, person_id, year, month)
    require_own_or_admin(current_user, wage_user_id, "Du kan bara ändra din egen lönespec")

    existing = (
        db.query(PayslipOverride)
        .filter(
            PayslipOverride.user_id == wage_user_id,
            PayslipOverride.year == year,
            PayslipOverride.month == month,
            PayslipOverride.row_key == row_key,
        )
        .first()
    )

    if not amount.strip():
        # An empty amount is how the UI removes an override: back to computed.
        if existing:
            db.delete(existing)
    else:
        values = {
            "amount": _parse_amount(amount),
            "hours": _parse_amount(hours) if hours.strip() else None,
            "reason": reason.strip() or None,
            "created_by": current_user.id,
        }
        if existing:
            for field, value in values.items():
                setattr(existing, field, value)
        else:
            db.add(PayslipOverride(user_id=wage_user_id, year=year, month=month, row_key=row_key, **values))

    db.commit()
    clear_schedule_cache()

    return RedirectResponse(url=f"/month/{person_id}/payslip?year={year}&month={month}", status_code=303)


@router.post("/month/{person_id}/payslip/compare", response_class=HTMLResponse, name="payslip_compare")
async def compare_payslip(
    request: Request,
    person_id: int,
    year: int = Form(...),
    month: int = Form(...),
    file: UploadFile = File(...),
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Parse an uploaded payslip PDF in memory and diff it against this month.

    The upload is read, parsed and dropped: nothing about it is stored, and the
    only thing that survives the request is the comparison rendered on the page.
    Anything unreadable becomes a message on the page, never a 500.
    """
    if current_user is None:
        return RedirectResponse(url=f"/login?next=/month/{person_id}/payslip", status_code=302)

    context = _build_context(request, person_id, year, month, current_user, db)

    # One byte past the limit is enough to know the file is too big, and nothing
    # beyond it is ever handed to the parser.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    error = None
    if not data:
        error = "payslip_upload_missing"
    elif len(data) > MAX_UPLOAD_BYTES:
        error = "payslip_upload_too_large"
    elif not data.startswith(b"%PDF"):
        error = "payslip_upload_not_pdf"
    else:
        try:
            wage_user = context["wage_user"]
            rates = (
                get_user_rates(wage_user, session=db, effective_date=date(year, month, 1))
                if wage_user is not None
                else None
            )
            parsed = parse_payslip_pdf(data, rates)
            context["comparison"] = compare_to_upload(context["slip"], parsed["rows"])
            context["parsed"] = parsed
        except Exception:
            logger.warning(
                "Lönespec kunde inte tolkas (user_id=%s, %s-%s)", current_user.id, year, month, exc_info=True
            )
            error = "payslip_upload_unreadable"

    context["upload_error"] = error
    return render("payslip.html", context)
