# app/routes/statistics.py
"""Statistics and trends routes - charts and visualizations for schedule data."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user_optional
from app.core.helpers import can_see_salary, strip_salary_data
from app.core.schedule import (
    _cached_special_rules,
    ob_rules,
    summarize_year_for_person,
)
from app.core.schedule import persons as person_list
from app.core.schedule.summary import apply_year_pay_adjustments
from app.core.utils import get_safe_today
from app.database.database import User, UserRole, get_db
from app.routes.shared import _resolve_person_param, render

router = APIRouter(prefix="/statistics", tags=["statistics"])


@router.get("/{person_id}", response_class=HTMLResponse, name="statistics_person")
async def statistics_view(
    request: Request,
    person_id: int,
    year: int = Query(None),
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Statistics and trend charts for a specific person."""
    if current_user is None:
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)

    # Resolve as a USER id when a User row exists, else legacy rotation position.
    target_user, rotation_position = _resolve_person_param(db, person_id)
    if target_user is not None:
        user_id_for_wages = target_user.id
        person_name = target_user.name
    else:
        user_id_for_wages = person_id
        person_name = None

    # Non-admin users can only view their own data
    if current_user.role != UserRole.ADMIN and current_user.id != user_id_for_wages:
        return RedirectResponse(
            url=f"/statistics/{current_user.id}?year={year or ''}",
            status_code=302,
        )

    from app.core.schedule import rotation_start_date

    safe_today = get_safe_today(rotation_start_date)
    year = year or safe_today.year

    # Resolve person name
    if person_name is None:
        if current_user.rotation_person_id == rotation_position:
            person_name = current_user.name
        else:
            holder = db.query(User).filter(User.person_id == rotation_position).first()
            if holder:
                person_name = holder.name
            else:
                holder = db.query(User).filter(User.id == rotation_position).first()
                person_name = holder.name if holder else person_list[rotation_position - 1].name

    # Fetch year data. For user-scoped views (a User resolved) filter months to
    # the viewed user's employment period regardless of the viewer's role.
    year_data = summarize_year_for_person(
        year,
        rotation_position,
        session=db,
        current_user=current_user,
        wage_user_id=user_id_for_wages,
        employment_user_id=target_user.id if target_user is not None else None,
    )
    months = year_data["months"]
    year_summary = year_data["year_summary"]

    show_salary = can_see_salary(current_user, rotation_position)

    if not show_salary:
        months = [strip_salary_data(m) for m in months]
        year_summary = strip_salary_data(year_summary)

    # Fold the vacation supplement and any employment transition into the pay
    # figures. Shared with /year/<id> so both pages show the same money.
    if show_salary:
        vac_user = (
            target_user
            if target_user is not None
            else db.query(User).filter(User.person_id == rotation_position).first()
        )
        if vac_user:
            apply_year_pay_adjustments(months, year_summary, vac_user, year, db)

    # Build chart data for template
    chart_labels = []
    chart_brutto = []
    chart_netto = []
    chart_ob = {"OB1": [], "OB2": [], "OB3": [], "OB4": [], "OB5": []}
    chart_oncall = []
    chart_hours = []

    for m in months:
        # The employment transition splits one payslip month into a consultant and a
        # direct-employer row; the extra row follows its month, so fold it into that bar.
        if m.get("transition_direct") and chart_labels:
            chart_brutto[-1] += round(m.get("brutto_pay", 0) or 0)
            chart_netto[-1] += round(m.get("netto_pay", 0) or 0)
            continue
        label = f"{m.get('payment_year', m['year'])}-{m.get('payment_month', m['month']):02d}"
        chart_labels.append(label)
        chart_brutto.append(round(m.get("brutto_pay", 0) or 0))
        chart_netto.append(round(m.get("netto_pay", 0) or 0))
        chart_oncall.append(round(m.get("oncall_pay", 0) or 0))
        chart_hours.append(round(m.get("total_hours", 0) or 0, 1))
        ob_pay = m.get("ob_pay", {})
        for code in chart_ob:
            chart_ob[code].append(round(ob_pay.get(code, 0) or 0))

    # OB rules for labels
    special_rules = _cached_special_rules(year)
    combined_rules = ob_rules + special_rules
    ob_labels = {}
    for rule in combined_rules:
        if rule.code not in ob_labels:
            ob_labels[rule.code] = rule.label

    # Absence summary for doughnut
    absence_data = {
        "sick": year_summary.get("total_sick_days", 0) or 0,
        "vab": year_summary.get("total_vab_days", 0) or 0,
        "leave": year_summary.get("total_leave_days", 0) or 0,
        "off": year_summary.get("total_off_days", 0) or 0,
        "parental": year_summary.get("total_parental_days", 0) or 0,
    }

    # Person list for admin navigation
    all_persons = None
    if current_user.role == UserRole.ADMIN:
        all_persons = db.query(User).filter(User.is_active == 1, User.role != UserRole.ADMIN).order_by(User.name).all()

    return render(
        "statistics.html",
        {
            "request": request,
            "user": current_user,
            "year": year,
            "person_id": person_id,
            "person_name": person_name,
            "months": months,
            "year_summary": year_summary,
            "show_salary": show_salary,
            "chart_labels": chart_labels,
            "chart_brutto": chart_brutto,
            "chart_netto": chart_netto,
            "chart_ob": chart_ob,
            "chart_oncall": chart_oncall,
            "chart_hours": chart_hours,
            "ob_labels": ob_labels,
            "absence_data": absence_data,
            "all_persons": all_persons,
        },
    )
