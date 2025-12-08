from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import datetime
from app.core.schedule import determine_shift_for_date, build_week_data, rotation_start_date


app = FastAPI()

templates = Jinja2Templates(directory="app/templates")


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
async def show_week_person(
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
async def show_week(
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
            
 