# from cgi import test
import datetime
import re
from unicodedata import category
from .storage import load_shift_types, load_rotation, load_settings

shift_types = load_shift_types()
rotation = load_rotation()
settings = load_settings()
rotation_start_date = datetime.datetime.strptime(settings.rotation_start_date, "%Y-%m-%d").date()

weekday_names = [
    "Måndag",
    "Tisdag",
    "Onsdag",
    "Torsdag",
    "Fredag",
    "Lördag",
    "Söndag",
]

def determine_shift_for_date(date: datetime.date, start_week: int = 1):
    
    if date < rotation_start_date:
        return None, None

    delta_days = (date - rotation_start_date).days
    rotation_week = str(((delta_days // 7) + (start_week - 1)) % rotation.rotation_length + 1)

    weekday_index = date.weekday()
    shift_code = rotation.weeks[rotation_week][weekday_index]

    for shift in shift_types:
        if shift.code == shift_code:
            return shift, rotation_week

    return None, None

def build_week_data(year: int, week: int, person_id: int | None = None):
    monday = datetime.date.fromisocalendar(year, week, 1)
    days_in_week = []

    person_ids = [person_id] if person_id is not None else list(range(1, 11))

    for offset in range(7):
        current_date = monday + datetime.timedelta(days=offset)
        weekday_index = current_date.weekday()
        weekday_name = weekday_names[weekday_index]
        day_info = {
            "date": current_date,
            "weekday_index": weekday_index,
            "weekday_name": weekday_name
        }

        if person_id is None:
            day_info["persons"] = []
            for pid in person_ids:
                result = determine_shift_for_date(current_date, start_week=pid)
                if result is None:
                    shift = None
                    rotation_week = None
                else:
                    shift, rotation_week = result
                person_data = {
                    "person_id": pid,
                    "shift": shift,
                    "rotation_week": rotation_week,
                }
                day_info["persons"].append(person_data)
        else:
            result = determine_shift_for_date(current_date, start_week=person_id)
            if result is None:
                shift = None
                rotation_week = None
            else:
                shift, rotation_week = result
            day_info["shift"] = shift
            day_info["rotation_week"] = rotation_week

        days_in_week.append(day_info)

    return days_in_week

def generate_year_data(year: int, person_id: int | None = None):
    start_date = datetime.date(year, 1, 1)
    end_date = datetime.date(year, 12, 31)
    days_in_year = []
    current_day = start_date
    if start_date < rotation_start_date:
        current_day = rotation_start_date
    person_ids = [person_id] if person_id is not None else list(range(1, 11))
    
    
    while current_day <= end_date:
        weekday_index = current_day.weekday()
        weekday_name = weekday_names[weekday_index]
        day_info = {
            "date": current_day,
            "weekday_index": weekday_index,
            "weekday_name": weekday_name
        }
        if person_id is None:
            day_info["persons"] = []
            for pid in person_ids:
                result = determine_shift_for_date(current_day, pid)
                if result is None:
                    shift = None
                    rotation_week = None
                    hours = 0.0
                    start = None
                    end = None
                else:
                    shift, rotation_week = result                    
                    hours, start, end = calculate_shift_hours(current_day, shift)
                person_data = {
                    "person_id": pid,
                    "shift": shift,
                    "rotation_week": rotation_week,
                    "hours": hours,
                    "start": start ,
                    "end": end,
                }
                day_info["persons"].append(person_data)
        else:
            result = determine_shift_for_date(current_day, person_id)
            if result is None:
                shift = None
                rotation_week = None
                hours = 0.0
                start = None
                end = None
            else:
                shift, rotation_week = result
                hours, start, end  = calculate_shift_hours(current_day, shift)

            day_info["shift"] = shift
            day_info["rotation_week"] = rotation_week
            day_info["hours"] = hours
            day_info["start"] = start
            day_info["end"] = end
            

        
        days_in_year.append(day_info)
        current_day = current_day + datetime.timedelta(days=1)
    return days_in_year


def calculate_shift_hours(date: datetime.date, shift):
    if shift is None or shift.code == "OFF":
        return 0.0, None, None
    
    start_time_dt = datetime.datetime.strptime(shift.start_time,"%H:%M")
    end_time_dt = datetime.datetime.strptime(shift.end_time,"%H:%M")

    start_t = start_time_dt.time()
    end_t = end_time_dt.time()

    start_datetime =  datetime.datetime.combine(date, start_t)
    end_datetime =  datetime.datetime.combine(date, end_t)

    if end_t <= start_t:
        end_datetime += datetime.timedelta(days=1)

    delta = end_datetime - start_datetime

    hours = delta.total_seconds() / 3600.0


    return hours, start_datetime, end_datetime

def summarize_year_by_month(year: int, person_id: int):
    days = generate_year_data(year, person_id)

    summary = {}
    for day in days:
        d = day["date"]
        month = d.month
        shift = day.get("shift")
        if month not in summary:
            summary[month] = {
                "total_hours": 0.0,
                "num_shifts": 0
            }
        summary[month]["total_hours"] += day["hours"]
        if not shift.code == "OFF":
            summary[month]["num_shifts"] += 1
    
    return summary


if __name__ == "__main__":
    import datetime

    test_date = 2026 
    results = generate_year_data(test_date, 1) 
    # for i in range(10):
    #     print("-", end="")
    #     print("-")
    #     for result in results:
    #         print(result["date"])
    #         print(result["weekday_name"])
    #         print(result)
    #         if not "persons" in result:
    #             print(result)
    #             for i in range(10):
    #                 print("-", end="") 
    #                 print("-")
    #         else:
    #             for person in result["persons"]:
    #                 print(person)
                    # for i in range(10):
                    #     print("-", end="") 
                    # print("-")
                
                

    person_id = 1
    person_ids = list(range(1, 2))
    for i in range(10):
        print("-", end="") 
    print("-")
    for person_id in person_ids:
        summary = summarize_year_by_month(test_date, person_id)
        print(f"Person ID: {person_id}")
        for month in sorted(summary.keys()):
            data = summary[month]
            print(f"Månad {month}: {data['total_hours']} timmar, {data['num_shifts']} pass")
        for i in range(10):
            print("-", end="") 
        print("-")

