import datetime
from .storage import load_shift_types, load_rotation, load_settings

shift_types = load_shift_types()
rotation = load_rotation()
settings = load_settings()
rotation_start_date = datetime.datetime.strptime(settings.rotation_start_date, "%Y-%m-%d").date()

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

def build_week_data(year: int, week: int, person_id: int = None):
    monday = datetime.date.fromisocalendar(year, week, 1)
    days_in_week = []

    person_ids = [person_id] if person_id else range(1, 11)

    for offset in range(7):
        current_date = monday + datetime.timedelta(days=offset)
        day_info = {
            "date": current_date,
            "weekday_index": current_date.weekday(),
        }

        if person_id is None:
            day_info["persons"] = []
            for pid in person_ids:
                shift, rotation_week = determine_shift_for_date(current_date, start_week=pid)
                person_data = {
                    "person_id": pid,
                    "shift": shift,
                    "rotation_week": rotation_week,
                }
                day_info["persons"].append(person_data)
        else:
            shift, rotation_week = determine_shift_for_date(current_date, start_week=person_id)
            day_info["shift"] = shift
            day_info["rotation_week"] = rotation_week

        days_in_week.append(day_info)

    return days_in_week

if __name__ == "__main__":
    import datetime

    test_date = datetime.date(2026, 12, 24)
    for week_start_index in range(10):
        result, rotation_week = determine_shift_for_date(test_date, start_week=week_start_index+1)
        print(f"Shift for {test_date} with start_week={week_start_index+1}: {result}, Rotation Week: {rotation_week}")

