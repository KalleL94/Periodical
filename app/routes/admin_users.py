# app/routes/admin_users.py
"""
Admin user management routes: create, edit, wages, rates, employment, transitions.
"""

import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_admin_user, get_password_hash, get_user_by_username
from app.core.constants import DEFAULT_PASSWORD
from app.core.logging_config import get_logger
from app.core.request_logging import log_auth_event
from app.core.schedule import clear_schedule_cache
from app.database.database import User, UserRole, get_db
from app.routes.shared import _parse_rates_form, templates

logger = get_logger(__name__)

router = APIRouter(tags=["admin"])


@router.get("/admin/users", response_class=HTMLResponse, name="admin_users")
async def admin_users_page(
    request: Request,
    success: str | None = Query(None),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: list all users."""
    users = db.query(User).order_by(User.id).all()
    return templates.TemplateResponse(
        "admin_users.html",
        {
            "request": request,
            "user": current_user,
            "users": users,
            "success": success,
        },
    )


@router.get("/admin/users/create", response_class=HTMLResponse, name="admin_create_user_page")
async def admin_create_user_page(
    request: Request,
    current_user: User = Depends(get_admin_user),
):
    """Admin: show create user form."""
    return templates.TemplateResponse(
        "admin_user_create.html",
        {
            "request": request,
            "user": current_user,
        },
    )


@router.post("/admin/users/create", name="admin_create_user")
async def admin_create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    name: str = Form(...),
    wage: int = Form(...),
    role: str = Form("user"),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: create new user."""
    if get_user_by_username(db, username):
        return templates.TemplateResponse(
            "admin_user_create.html",
            {"request": request, "user": current_user, "error": "Användarnamnet finns redan"},
            status_code=400,
        )

    new_user = User(
        username=username,
        password_hash=get_password_hash(password),
        name=name,
        wage=wage,
        role=UserRole(role),
        vacation={},
        must_change_password=1,
    )
    db.add(new_user)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return RedirectResponse(url="/admin/users", status_code=302)


@router.get("/admin/users/{user_id}", response_class=HTMLResponse, name="admin_edit_user_page")
async def admin_edit_user_page(
    request: Request,
    user_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: show edit user form."""
    from app.core.rates import get_all_defaults, get_rate_history
    from app.core.schedule import get_wage_history
    from app.core.schedule.person_history import get_user_history
    from app.core.schedule.transition import (
        calculate_consultant_vacation_days,
        calculate_variable_avg_daily,
        get_earning_year,
    )
    from app.core.schedule.vacation import calculate_vacation_balance
    from app.core.storage import get_available_tax_tables
    from app.core.utils import get_today
    from app.database.database import EmploymentTransition

    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")

    available_tax_tables = get_available_tax_tables()
    wage_history = get_wage_history(db, user_id)
    person_history = get_user_history(db, user_id)
    vacation_balance = calculate_vacation_balance(edit_user, get_today().year, db)

    edit_transition = db.query(EmploymentTransition).filter(EmploymentTransition.user_id == edit_user.id).first()
    admin_auto_variable_avg = None
    admin_auto_vacation_days = None
    if edit_transition:
        if edit_transition.variable_avg_daily_override is None:
            earning_start, earning_end = get_earning_year(edit_transition)
            admin_auto_variable_avg = calculate_variable_avg_daily(edit_user, db, earning_start, earning_end)
        admin_auto_vacation_days = calculate_consultant_vacation_days(edit_user, edit_transition)

    return templates.TemplateResponse(
        "admin_user_edit.html",
        {
            "request": request,
            "user": current_user,
            "edit_user": edit_user,
            "available_tax_tables": available_tax_tables,
            "wage_history": wage_history,
            "person_history": person_history,
            "vacation_balance": vacation_balance,
            "rate_defaults": get_all_defaults(),
            "custom_rates": edit_user.custom_rates or {},
            "rate_history": get_rate_history(db, edit_user.id),
            "edit_transition": edit_transition,
            "admin_auto_variable_avg": admin_auto_variable_avg,
            "admin_auto_vacation_days": admin_auto_vacation_days,
            "salary_types": [
                ("trailing", "Släpande (lön för föregående månad)"),
                ("current", "Innestående (lön för aktuell månad)"),
            ],
        },
    )


@router.post("/admin/users/{user_id}", name="admin_update_user")
async def admin_update_user(
    request: Request,
    user_id: int,
    name: str = Form(...),
    role: str = Form("user"),
    person_id: int | None = Form(None),
    tax_table: str | None = Form(None),
    new_password: str = Form(None),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: update user."""
    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")

    edit_user.name = name
    edit_user.role = UserRole(role)
    edit_user.person_id = person_id
    edit_user.tax_table = tax_table if tax_table else None

    if new_password:
        if len(new_password) < 8:
            return templates.TemplateResponse(
                "admin_user_edit.html",
                {
                    "request": request,
                    "user": current_user,
                    "edit_user": edit_user,
                    "error": "Nytt lösenord måste vara minst 8 tecken",
                },
                status_code=400,
            )
        edit_user.password_hash = get_password_hash(new_password)
        edit_user.must_change_password = 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return RedirectResponse(url="/admin/users", status_code=302)


@router.post("/admin/users/{user_id}/reset-password", name="admin_reset_password")
async def admin_reset_password(
    request: Request,
    user_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: reset user password to default and force password change."""
    reset_user = db.query(User).filter(User.id == user_id).first()
    if not reset_user:
        raise HTTPException(status_code=404, detail="User not found")

    default_password = DEFAULT_PASSWORD
    reset_user.password_hash = get_password_hash(default_password)
    reset_user.must_change_password = 1
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    clear_schedule_cache()

    log_auth_event(
        event_type="password_reset",
        username=reset_user.username,
        user_id=reset_user.id,
        success=True,
        details={"reset_by": current_user.username, "reset_by_id": current_user.id},
    )

    from urllib.parse import quote

    success_msg = f"Lösenordet för {reset_user.name} har återställts till {default_password}"
    return RedirectResponse(url=f"/admin/users?success={quote(success_msg)}", status_code=302)


@router.post("/admin/users/{user_id}/add-wage", name="admin_add_wage")
async def admin_add_wage(
    request: Request,
    user_id: int,
    new_wage: int = Form(...),
    effective_from: str = Form(...),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: add a new wage with effective date."""
    from datetime import datetime

    from app.core.schedule import add_new_wage

    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        effective_date = datetime.strptime(effective_from, "%Y-%m-%d").date()
        add_new_wage(
            session=db,
            user_id=user_id,
            new_wage=new_wage,
            effective_from=effective_date,
            created_by=current_user.id,
        )
        clear_schedule_cache()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Ogiltigt datum: {e}") from e
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Kunde inte lägga till lön: {e}") from e

    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)


@router.post("/admin/users/{user_id}/delete-wage/{wage_id}", name="admin_delete_wage")
async def admin_delete_wage(
    request: Request,
    user_id: int,
    wage_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: delete a wage history entry for any user."""
    from app.database.database import WageHistory

    wage_record = db.query(WageHistory).filter(WageHistory.id == wage_id).first()

    if not wage_record:
        raise HTTPException(status_code=404, detail="Wage record not found")

    if wage_record.user_id != user_id:
        raise HTTPException(status_code=400, detail="Wage record does not belong to this user")

    total_wages = db.query(WageHistory).filter(WageHistory.user_id == user_id).count()

    if total_wages <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the only wage record")

    if wage_record.effective_to is None:
        previous_wage = (
            db.query(WageHistory)
            .filter(
                WageHistory.user_id == user_id,
                WageHistory.id != wage_id,
                WageHistory.effective_to.isnot(None),
            )
            .order_by(WageHistory.effective_from.desc())
            .first()
        )

        if previous_wage:
            previous_wage.effective_to = None
            edit_user = db.query(User).filter(User.id == user_id).first()
            if edit_user:
                edit_user.wage = previous_wage.wage

    db.delete(wage_record)
    db.commit()
    clear_schedule_cache()

    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)


@router.post("/admin/users/{user_id}/update-rates", name="admin_update_rates")
async def admin_update_rates(
    request: Request,
    user_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: add new rate entry with effective date for a user."""
    from app.core.rates import add_new_rates

    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")

    form = await request.form()
    rates = _parse_rates_form(form)
    effective_from = form.get("effective_from", "").strip()

    if not effective_from:
        raise HTTPException(status_code=400, detail="Från-datum krävs")

    try:
        effective_date = datetime.datetime.strptime(effective_from, "%Y-%m-%d").date()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Ogiltigt datum: {e}") from e

    add_new_rates(
        session=db,
        user_id=user_id,
        rates=rates,
        effective_from=effective_date,
        created_by=current_user.id,
    )
    clear_schedule_cache()

    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)


@router.post("/admin/users/{user_id}/delete-rate/{rate_id}", name="admin_delete_rate")
async def admin_delete_rate(
    user_id: int,
    rate_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: delete a rate history entry for a user."""
    from app.core.rates import delete_rate_history

    delete_rate_history(db, rate_id, user_id)
    clear_schedule_cache()

    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)


@router.post("/admin/users/{user_id}/start-employment", name="admin_start_employment")
async def admin_start_employment(
    request: Request,
    user_id: int,
    person_id: int = Form(...),
    start_date: str = Form(...),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: start a person's employment at a position."""
    from datetime import datetime

    from app.core.schedule.person_history import start_employment

    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
        start_employment(
            session=db,
            user_id=user_id,
            person_id=person_id,
            name=edit_user.name,
            username=edit_user.username,
            start_date=start_date_obj,
            created_by=current_user.id,
        )
        return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)

    except ValueError as e:
        return templates.TemplateResponse(
            "admin_user_edit.html",
            {
                "request": request,
                "user": current_user,
                "edit_user": edit_user,
                "error": f"Ogiltigt datumformat: {e}",
            },
            status_code=400,
        )


@router.post("/admin/users/{user_id}/end-employment", name="admin_end_employment")
async def admin_end_employment(
    request: Request,
    user_id: int,
    person_id: int = Form(...),
    end_date: str = Form(...),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: end a person's employment."""
    from datetime import datetime

    from app.core.schedule.person_history import end_employment

    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
        end_employment(
            session=db,
            user_id=user_id,
            person_id=person_id,
            end_date=end_date_obj,
        )
        return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)

    except ValueError as e:
        return templates.TemplateResponse(
            "admin_user_edit.html",
            {
                "request": request,
                "user": current_user,
                "edit_user": edit_user,
                "error": f"Ogiltigt datumformat: {e}",
            },
            status_code=400,
        )


@router.post("/admin/users/{user_id}/delete-employment/{history_id}", name="admin_delete_employment")
async def admin_delete_employment(
    request: Request,
    user_id: int,
    history_id: int,
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: delete a person history entry."""
    from app.database.database import PersonHistory

    history_record = db.query(PersonHistory).filter(PersonHistory.id == history_id).first()

    if not history_record:
        raise HTTPException(status_code=404, detail="Employment record not found")

    if history_record.user_id != user_id:
        raise HTTPException(status_code=400, detail="Employment record does not belong to this user")

    person_id = history_record.person_id

    if history_record.effective_to is None:
        previous_record = (
            db.query(PersonHistory)
            .filter(
                PersonHistory.person_id == person_id,
                PersonHistory.id != history_id,
                PersonHistory.effective_to.isnot(None),
            )
            .order_by(PersonHistory.effective_from.desc())
            .first()
        )

        if previous_record:
            previous_record.effective_to = None

        edit_user = db.query(User).filter(User.id == user_id).first()
        if edit_user and edit_user.person_id == person_id:
            edit_user.person_id = None

    remaining_records = (
        db.query(PersonHistory).filter(PersonHistory.user_id == user_id, PersonHistory.id != history_id).count()
    )

    if remaining_records == 0:
        edit_user = db.query(User).filter(User.id == user_id).first()
        if edit_user:
            edit_user.is_active = 0
            edit_user.person_id = None

    db.delete(history_record)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    clear_schedule_cache()

    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)


@router.post("/admin/users/{user_id}/transition", name="admin_transition_save")
async def admin_transition_save(
    request: Request,
    user_id: int,
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
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: spara anställningsövergång för en användare."""
    import datetime as _dt

    from app.database.database import ConsultantSalaryType, EmploymentTransition

    edit_user = db.query(User).filter(User.id == user_id).first()
    if not edit_user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        t_date = _dt.date.fromisoformat(transition_date)
    except ValueError:
        return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)

    if consultant_salary_type not in ("trailing", "current"):
        return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)

    salary_type = ConsultantSalaryType(consultant_salary_type)

    if consultant_vacation_days.strip():
        try:
            parsed_vacation_days = float(consultant_vacation_days.strip())
        except ValueError:
            parsed_vacation_days = 0.0
    else:
        from types import SimpleNamespace

        from app.core.schedule.transition import calculate_consultant_vacation_days

        temp = SimpleNamespace(
            transition_date=t_date,
            earning_year_start=None,
            earning_year_end=None,
        )
        parsed_vacation_days = float(calculate_consultant_vacation_days(edit_user, temp) or 0)

    variable_override: float | None = None
    if variable_avg_daily_override.strip():
        try:
            variable_override = float(variable_avg_daily_override.strip())
        except ValueError:
            pass

    earning_start: _dt.date | None = None
    earning_end: _dt.date | None = None
    if earning_year_start.strip():
        try:
            earning_start = _dt.date.fromisoformat(earning_year_start.strip())
        except ValueError:
            pass
    if earning_year_end.strip():
        try:
            earning_end = _dt.date.fromisoformat(earning_year_end.strip())
        except ValueError:
            pass

    transition = db.query(EmploymentTransition).filter(EmploymentTransition.user_id == user_id).first()
    if transition is None:
        transition = EmploymentTransition(user_id=user_id)
        db.add(transition)

    transition.transition_date = t_date
    transition.consultant_salary_type = salary_type
    transition.consultant_vacation_days = parsed_vacation_days
    transition.consultant_supplement_pct = consultant_supplement_pct
    transition.variable_avg_daily_override = variable_override
    transition.earning_year_start = earning_start
    transition.earning_year_end = earning_end
    transition.notes = notes.strip() or None
    transition.updated_at = _dt.datetime.utcnow()

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    if new_direct_salary.strip():
        try:
            salary_int = int(new_direct_salary.strip())
            from app.core.schedule import add_new_wage
            from app.database.database import WageHistory

            existing_wage = (
                db.query(WageHistory)
                .filter(
                    WageHistory.user_id == user_id,
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
                    user_id=user_id,
                    new_wage=salary_int,
                    effective_from=t_date,
                    created_by=current_user.id,
                )
            clear_schedule_cache()
        except (ValueError, Exception):
            pass

    if reset_rates_to_default.strip():
        from app.core.rates import add_new_rates

        add_new_rates(
            session=db,
            user_id=user_id,
            rates={},
            effective_from=t_date,
            created_by=current_user.id,
        )
        clear_schedule_cache()

    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)


@router.post("/admin/users/{user_id}/transition/delete", name="admin_transition_delete")
async def admin_transition_delete(
    user_id: int,
    cleanup_wage: str = Form(""),
    cleanup_rates: str = Form(""),
    current_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin: ta bort anställningsövergång för en användare."""
    from app.database.database import EmploymentTransition, RateHistory, WageHistory

    transition = db.query(EmploymentTransition).filter(EmploymentTransition.user_id == user_id).first()
    if transition:
        t_date = transition.transition_date
        if cleanup_wage.strip():
            db.query(WageHistory).filter(
                WageHistory.user_id == user_id,
                WageHistory.effective_from == t_date,
            ).delete()
        if cleanup_rates.strip():
            db.query(RateHistory).filter(
                RateHistory.user_id == user_id,
                RateHistory.effective_from == t_date,
            ).delete()
        db.delete(transition)
        try:
            db.commit()
            clear_schedule_cache()
        except Exception:
            db.rollback()
            raise

    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)
