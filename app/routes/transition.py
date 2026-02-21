# app/routes/transition.py
"""
Anställningsövergång — routes för konsult → direktanställning.

Tillgänglig för inloggad användare via /profile/transition.
"""

import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user
from app.database.database import ConsultantSalaryType, EmploymentTransition, User, get_db
from app.routes.shared import templates

router = APIRouter(tags=["transition"])


def _get_transition_context(
    request: Request,
    user: User,
    db: Session,
    error: str | None = None,
) -> dict:
    """Bygg template-kontext för transition-sidan."""
    from app.core.schedule.transition import (
        calculate_consultant_vacation_days,
        calculate_transition_month_summary,
        calculate_variable_avg_daily,
        get_earning_year,
    )

    transition = db.query(EmploymentTransition).filter(EmploymentTransition.user_id == user.id).first()

    # Auto-beräkna rörlig genomsnittslön och semesterdagar om transition finns
    auto_variable_avg = None
    auto_consultant_vacation_days = None
    preview = None
    if transition:
        earning_start, earning_end = get_earning_year(transition)
        if transition.variable_avg_daily_override is None:
            auto_variable_avg = calculate_variable_avg_daily(user, db, earning_start, earning_end)
        auto_consultant_vacation_days = calculate_consultant_vacation_days(user, transition)
        try:
            preview = calculate_transition_month_summary(transition, user, db)
        except Exception:
            preview = None

    return {
        "request": request,
        "user": user,
        "transition": transition,
        "salary_types": [
            ("trailing", "Släpande (lön för föregående månad)"),
            ("current", "Innestående (lön för aktuell månad)"),
        ],
        "auto_variable_avg": auto_variable_avg,
        "auto_consultant_vacation_days": auto_consultant_vacation_days,
        "preview": preview,
        "error": error,
    }


@router.get("/profile/transition", response_class=HTMLResponse, name="transition_page")
async def transition_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Visa inställningssida för anställningsövergång."""
    ctx = _get_transition_context(request, current_user, db)
    return templates.TemplateResponse("transition.html", ctx)


@router.post("/profile/transition", name="transition_save")
async def transition_save(
    request: Request,
    transition_date: str = Form(...),
    consultant_salary_type: str = Form(...),
    consultant_vacation_days: str = Form(""),
    consultant_supplement_pct: float = Form(...),
    variable_avg_daily_override: str = Form(""),
    earning_year_start: str = Form(""),
    earning_year_end: str = Form(""),
    notes: str = Form(""),
    new_direct_salary: str = Form(""),
    reset_rates_to_default: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Spara eller uppdatera anställningsövergång. PRG-redirect."""
    # Validering
    try:
        t_date = datetime.date.fromisoformat(transition_date)
    except ValueError:
        ctx = _get_transition_context(request, current_user, db, error="Ogiltigt övergångsdatum.")
        return templates.TemplateResponse("transition.html", ctx, status_code=400)

    if consultant_salary_type not in ("trailing", "current"):
        ctx = _get_transition_context(request, current_user, db, error="Ogiltig lönetyp.")
        return templates.TemplateResponse("transition.html", ctx, status_code=400)

    if not (0 < consultant_supplement_pct < 1):
        ctx = _get_transition_context(
            request, current_user, db, error="Tilläggsprocent måste vara mellan 0 och 1 (t.ex. 0.0043)."
        )
        return templates.TemplateResponse("transition.html", ctx, status_code=400)

    # Parsning av optionella fält
    variable_override: float | None = None
    if variable_avg_daily_override.strip():
        try:
            variable_override = float(variable_avg_daily_override.strip())
        except ValueError:
            ctx = _get_transition_context(request, current_user, db, error="Ogiltig rörlig genomsnittslön.")
            return templates.TemplateResponse("transition.html", ctx, status_code=400)

    earning_start: datetime.date | None = None
    earning_end: datetime.date | None = None
    if earning_year_start.strip():
        try:
            earning_start = datetime.date.fromisoformat(earning_year_start.strip())
        except ValueError:
            ctx = _get_transition_context(request, current_user, db, error="Ogiltigt startdatum för intjänandeår.")
            return templates.TemplateResponse("transition.html", ctx, status_code=400)
    if earning_year_end.strip():
        try:
            earning_end = datetime.date.fromisoformat(earning_year_end.strip())
        except ValueError:
            ctx = _get_transition_context(request, current_user, db, error="Ogiltigt slutdatum för intjänandeår.")
            return templates.TemplateResponse("transition.html", ctx, status_code=400)

    # Semesterdagar: manuell override eller auto-beräknat från anställningsdatum
    parsed_vacation_days: float
    if consultant_vacation_days.strip():
        try:
            parsed_vacation_days = float(consultant_vacation_days.strip())
        except ValueError:
            ctx = _get_transition_context(request, current_user, db, error="Ogiltigt antal semesterdagar.")
            return templates.TemplateResponse("transition.html", ctx, status_code=400)
    else:
        from types import SimpleNamespace

        from app.core.schedule.transition import calculate_consultant_vacation_days

        temp = SimpleNamespace(
            transition_date=t_date,
            earning_year_start=earning_start,
            earning_year_end=earning_end,
        )
        parsed_vacation_days = float(calculate_consultant_vacation_days(current_user, temp) or 0)

    salary_type = ConsultantSalaryType(consultant_salary_type)

    # Hämta eller skapa transition-post
    transition = db.query(EmploymentTransition).filter(EmploymentTransition.user_id == current_user.id).first()
    if transition is None:
        transition = EmploymentTransition(user_id=current_user.id)
        db.add(transition)

    transition.transition_date = t_date
    transition.consultant_salary_type = salary_type
    transition.consultant_vacation_days = parsed_vacation_days
    transition.consultant_supplement_pct = consultant_supplement_pct
    transition.variable_avg_daily_override = variable_override
    transition.earning_year_start = earning_start
    transition.earning_year_end = earning_end
    transition.notes = notes.strip() or None
    transition.updated_at = datetime.datetime.utcnow()

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Sätt ny direktlön från övergångsdatum
    if new_direct_salary.strip():
        try:
            salary_int = int(new_direct_salary.strip())
            from app.core.schedule import add_new_wage, clear_schedule_cache
            from app.database.database import WageHistory

            existing_wage = (
                db.query(WageHistory)
                .filter(
                    WageHistory.user_id == current_user.id,
                    WageHistory.effective_from == t_date,
                )
                .first()
            )
            if existing_wage:
                existing_wage.wage = salary_int
                db.commit()
            else:
                add_new_wage(
                    session=db,
                    user_id=current_user.id,
                    new_wage=salary_int,
                    effective_from=t_date,
                    created_by=current_user.id,
                )
            clear_schedule_cache()
        except (ValueError, Exception):
            pass

    # Återgå till standardsatser (OB/OT/beredskap) från övergångsdatum
    if reset_rates_to_default.strip():
        from app.core.rates import add_new_rates
        from app.core.schedule import clear_schedule_cache

        add_new_rates(
            session=db,
            user_id=current_user.id,
            rates={},
            effective_from=t_date,
            created_by=current_user.id,
        )
        clear_schedule_cache()

    return RedirectResponse(url="/profile/transition", status_code=302)


@router.post("/profile/transition/delete", name="transition_delete")
async def transition_delete(
    request: Request,
    cleanup_wage: str = Form(""),
    cleanup_rates: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Ta bort transition-konfiguration och valfritt associerade lon/sats-poster."""
    from app.core.schedule import clear_schedule_cache
    from app.database.database import RateHistory, WageHistory

    transition = db.query(EmploymentTransition).filter(EmploymentTransition.user_id == current_user.id).first()
    if transition:
        t_date = transition.transition_date
        if cleanup_wage.strip():
            db.query(WageHistory).filter(
                WageHistory.user_id == current_user.id,
                WageHistory.effective_from == t_date,
            ).delete()
        if cleanup_rates.strip():
            db.query(RateHistory).filter(
                RateHistory.user_id == current_user.id,
                RateHistory.effective_from == t_date,
            ).delete()
        db.delete(transition)
        try:
            db.commit()
            clear_schedule_cache()
        except Exception:
            db.rollback()
            raise

    return RedirectResponse(url="/profile/transition", status_code=302)
