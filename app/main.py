from fastapi import FastAPI, Request, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import *
import datetime
from app.core.schedule import (
    determine_shift_for_date,
    build_week_data,
    rotation_start_date,
    summarize_month_for_person,
    summarize_year_for_person,
    generate_year_data,
    calculate_shift_hours,
    build_special_ob_rules_for_year,
    ob_rules,
    calculate_ob_hours,
    calculate_ob_pay,
    _cached_special_rules,
    _select_ob_rules_for_date,
    settings,
    weekday_names,
    person_wages,
    build_cowork_stats,
    build_cowork_details,
    persons as person_list,
)


app = FastAPI()
app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")


def _contrast_color(hex_color: str) -> str:
    """Return '#000' for light backgrounds, '#fff' for dark backgrounds."""
    if not hex_color:
        return "#fff"
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join([c*2 for c in h])
    try:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
    except Exception:
        return "#fff"
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#000" if lum > 0.5 else "#fff"


# Register a Jinja filter so templates can call `|contrast`
templates.env.filters["contrast"] = _contrast_color

def get_prev_next_week(year: int, week: int):
    monday = datetime.date.fromisocalendar(year, week, 1)

    prev_monday = monday - datetime.timedelta(weeks=1)
    next_monday = monday + datetime.timedelta(weeks=1)

    prev_year, prev_week, _ = prev_monday.isocalendar()
    next_year, next_week, _ = next_monday.isocalendar()

    return (prev_year, prev_week), (next_year, next_week)


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/day/{person_id}/{year}/{month}/{day}", response_class=HTMLResponse, name="day_person")
async def show_day_for_person(
    request: Request,
    person_id: int,
    year: int,
    month: int,
    day: int,
):
    date = datetime.date(year, month, day)

    prev_date = date - datetime.timedelta(days=1)
    next_date = date + datetime.timedelta(days=1)
    iso_year, iso_week, _ = date.isocalendar()

    # Shift / hours
    shift, rotation_week = determine_shift_for_date(date, start_week=person_id)
    hours: float = 0.0
    start_dt: datetime.datetime | None = None
    end_dt: datetime.datetime | None = None

    if shift and shift.code != "OFF":
        hours, start_dt, end_dt = calculate_shift_hours(date, shift)

    # Special OB rules for this year (cached)
    special_rules = _cached_special_rules(year)
    combined_rules = ob_rules + special_rules

    # OB hours and pay
    person = person_list[person_id - 1]
    monthly_salary = person_wages.get(person_id, settings.monthly_salary)

    if start_dt and end_dt:
        ob_hours = calculate_ob_hours(start_dt, end_dt, combined_rules)
        ob_pay = calculate_ob_pay(start_dt, end_dt, combined_rules, monthly_salary)
    else:
        # No worked hours; keep base codes at zero
        ob_hours = {r.code: 0.0 for r in ob_rules}
        ob_pay = {r.code: 0.0 for r in ob_rules}

    ob_codes = sorted(ob_hours.keys())

    # Active special OB rules for this calendar day
    midnight = datetime.datetime.combine(date, datetime.time(0, 0))
    active_special_rules = _select_ob_rules_for_date(midnight, special_rules)

    weekday_name = weekday_names[date.weekday()]

    return templates.TemplateResponse(
        "day.html",
        {
            "request": request,
            "person_id": person_id,
            "person_name": person.name,
            "date": date,
            "weekday_name": weekday_name,
            "rotation_week": rotation_week,
            "shift": shift,
            "hours": hours,
            "ob_hours": ob_hours,
            "ob_pay": ob_pay,
            "ob_codes": ob_codes,
            "active_special_rules": active_special_rules,
            "prev_year": prev_date.year,
            "prev_month": prev_date.month,
            "prev_day": prev_date.day,
            "next_year": next_date.year,
            "next_month": next_date.month,
            "next_day": next_date.day,
            "iso_year": iso_year,
            "iso_week": iso_week,
        },
    )

@app.get("/week/{person_id}", response_class=HTMLResponse, name="week_person")
async def show_week_for_person(
    request: Request,
    person_id: int,
    year: int = None,
    week: int = None,
):
    today = datetime.date.today()
    
    if today < rotation_start_date:
        today = rotation_start_date
        
    iso_year, iso_week, _ = today.isocalendar()
    
    year = year or iso_year
    week = week or iso_week
    
    days_in_week = build_week_data(year, week, person_id=person_id)

    (prev_year, prev_week), (next_year, next_week) = get_prev_next_week(year, week)
        
    today = datetime.date.today()
    return templates.TemplateResponse(
        "week.html",
        {
            "request": request,
            "year": year,
            "week": week,
            "days": days_in_week,
            "person_id": person_id,
            "prev_year": prev_year,
            "prev_week": prev_week,
            "next_year": next_year,
            "next_week": next_week,
            "today": today,
        },
    )

@app.get("/week", response_class=HTMLResponse, name="week_all")
async def show_week_all(
    request: Request,
    year: int = None,
    week: int = None,
):
    today = datetime.date.today()
    
    if today < rotation_start_date:
        today = rotation_start_date
        
    iso_year, iso_week, _ = today.isocalendar()
    
    year = year or iso_year
    week = week or iso_week
    
    days_in_week = build_week_data(year, week)

    (prev_year, prev_week), (next_year, next_week) = get_prev_next_week(year, week)
    
    today = datetime.date.today()
    return templates.TemplateResponse(
        "week_all.html",
        {
            "request": request,
            "year": year,
            "week": week,
            "days": days_in_week,
            "prev_year": prev_year,
            "prev_week": prev_week,
            "next_year": next_year,
            "next_week": next_week,
            "today": today,
        },
    )

@app.get("/month/{person_id}", response_class=HTMLResponse, name="month_person")
async def show_month_for_person(
    request: Request,
    person_id: int,
    year: int = None,
    month: int = None,
):
    today = datetime.date.today()
    
    if today < rotation_start_date:
        today = rotation_start_date
        
    year = year or today.year
    month = month or today.month
    
    days_in_month = summarize_month_for_person(year, month, person_id=person_id)
        
    return templates.TemplateResponse(
        "month.html",
        {
            "request": request,
            "year": year,
            "month": month,
            "person_id": person_id,
            "person_name": person_list[person_id - 1].name,
            "days": days_in_month,
        },
    )


@app.get("/month", response_class=HTMLResponse, name="month_all")
async def show_month_all(
    request: Request,
    year: int = None,
    month: int = None,
):
    today = datetime.date.today()

    if today < rotation_start_date:
        today = rotation_start_date

    year = year or today.year
    month = month or today.month

    # Build summary for all 10 persons
    persons = []
    for pid in range(1, 11):
        summary = summarize_month_for_person(year, month, pid)
        persons.append(summary)

    return templates.TemplateResponse(
        "month_all.html",
        {
            "request": request,
            "year": year,
            "month": month,
            "persons": persons,
        },
    )


@app.get("/year/{person_id}", response_class=HTMLResponse, name="year_person")
async def year_view(
    request: Request,
    person_id: int,
    year: int = Query(None),
    with_person_id: int | None = Query(None, alias="with_person_id"),
):
    today = datetime.date.today()
    if today < rotation_start_date:
        today = rotation_start_date
    if year is None:
        year = today.year

    person = person_list[person_id - 1]

    # Cowork data
    cowork_rows = build_cowork_stats(year, person_id)
    selected_other_id = None
    selected_other_name = None
    cowork_details: list[dict] = []

    if with_person_id:
        selected_other_id = with_person_id
        selected_other_name = persons[with_person_id - 1].name
        cowork_details = build_cowork_details(year, person_id, with_person_id)

    # Year summary and per month data
    year_data = summarize_year_for_person(year, person_id)
    months = year_data["months"]
    year_summary = year_data["year_summary"]

    return templates.TemplateResponse(
        "year.html",
        {
            "request": request,
            "year": year,
            "person_id": person_id,
            "person_name": person.name,
            "months": months,
            "year_summary": year_summary,
            "cowork_rows": cowork_rows,
            "selected_other_id": selected_other_id,
            "selected_other_name": selected_other_name,
            "cowork_details": cowork_details,
        },
    )
# async def show_year_for_person(
#     request: Request,
#     person_id: int,
#     year: int = None,
#     with_person_id: int | None = None,
# ):
#     today = datetime.date.today()

#     if today < rotation_start_date:
#         today = rotation_start_date

#     year = year or today.year

#     months = []
#     for m in range(1, 13):
#         months.append(summarize_month_for_person(year, m, person_id))
        
#     cowork_rows = build_cowork_stats(year, person_id)
    
#     cowork_details: List[Dict] = []
#     selected_other_name: str | None = None
    
#     if with_person_id:
#         cowork_details = build_cowork_details(year, person_id, with_person_id)
#         selected_other_name = person_list[with_person_id - 1].name
        
#     person_name = person_list[person_id - 1].name

#     return templates.TemplateResponse(
#         "year.html",
#         {
#             "request": request,
#             "year": year,
#             "person_id": person_id,
#             "person_name": person_name,
#             "months": months,
#             "cowork_rows": cowork_rows,
#             "cowork_details": cowork_details,
#             "selected_other_id": with_person_id,
#             "selected_other_name": selected_other_name,
#         },
#     )


@app.get("/year", response_class=HTMLResponse, name="year_all")
async def show_year_all(
    request: Request,
    year: int = None,
):
    today = datetime.date.today()

    if today < rotation_start_date:
        today = rotation_start_date

    year = year or today.year
    # Build full-year day list with per-person shifts using generate_year_data
    days_in_year = generate_year_data(year)
    # Compute total OB pay per person for the year by summing monthly summaries
    person_ob_totals = []
    for pid in range(1, 11):
        total_pay = 0.0
        for m in range(1, 13):
            msum = summarize_month_for_person(year, m, pid)
            total_pay += sum(msum.get("ob_pay", {}).values())
        person_ob_totals.append(total_pay)

    return templates.TemplateResponse(
        "year_all.html",
        {
            "request": request,
            "year": year,
            "days": days_in_year,
            "person_ob_totals": person_ob_totals,
        },
    )