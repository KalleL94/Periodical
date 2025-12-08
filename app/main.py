from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import datetime
from app.core.schedule import (
    determine_shift_for_date,
    build_week_data,
    rotation_start_date,
    summarize_month_for_person,
    generate_year_data,
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


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/today")
async def show_today(request: Request):
    #set day to 6/3/2026
    today = datetime.date(2026, 3, 6)
    shifts = []
    for i in range(10):
        shift, rotation_week = determine_shift_for_date(today, start_week=i+1)
        data = {
            "start_week": i + 1,
            "shift": shift,
            "rotation_week": rotation_week,
        }
        shifts.append(data)

    return templates.TemplateResponse(
        "test_today.html",
        {
            "request": request,
            "today": today,
            "shifts": shifts
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
        
    return templates.TemplateResponse(
        "week.html",
        {
            "request": request,
            "year": year,
            "week": week,
            "days": days_in_week,
            "person_id": person_id,
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
        
    return templates.TemplateResponse(
        "week_all.html",
        {
            "request": request,
            "year": year,
            "week": week,
            "days": days_in_week,
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
async def show_year_for_person(
    request: Request,
    person_id: int,
    year: int = None,
):
    today = datetime.date.today()

    if today < rotation_start_date:
        today = rotation_start_date

    year = year or today.year

    months = []
    for m in range(1, 13):
        months.append(summarize_month_for_person(year, m, person_id))

    return templates.TemplateResponse(
        "year.html",
        {
            "request": request,
            "year": year,
            "person_id": person_id,
            "months": months,
        },
    )


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