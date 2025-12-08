# from cgi import test
import datetime
import re
from unicodedata import category
from .storage import load_shift_types, load_rotation, load_settings, load_ob_rules, load_tax_brackets, calculate_tax_bracket, load_persons
from .holidays import *
from .models import ObRule

shift_types = load_shift_types()
rotation = load_rotation()
settings = load_settings()
ob_rules = load_ob_rules()
tax_brackets = load_tax_brackets()
persons = load_persons()
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

    days_to_first_monday = (7 - rotation_start_date.weekday()) % 7
    if days_to_first_monday == 0 and rotation_start_date.weekday() != 0:
        days_to_first_monday = 7 - rotation_start_date.weekday()

    first_monday = rotation_start_date + datetime.timedelta(days=days_to_first_monday)

    delta_days = (date - rotation_start_date).days

    if date < first_monday:
        weeks_passed = 0
    else:
        days_to_first_monday = (date - first_monday).days
        weeks_passed = 1 + (days_to_first_monday // 7)

    rotation_week = str(((weeks_passed + (start_week - 1)) % rotation.rotation_length) + 1)
    
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
                    "person_name": persons[pid - 1].name,
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
            day_info["person_id"] = person_id
            day_info["person_name"] = persons[person_id - 1].name

        days_in_week.append(day_info)

    return days_in_week

def generate_year_data(year: int, person_id: int | None = None):
    special_ob_rules = build_special_ob_rules_for_year(year)
    combined_ob_rules = ob_rules + special_ob_rules
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
                    "person_name": persons[pid - 1].name,
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
                if start is not None:
                    ob = calculate_ob_hours(start, end, combined_ob_rules)
                else:
                    ob = {}

            day_info["person_id"] = person_id
            day_info["person_name"] = persons[person_id - 1].name
            day_info["shift"] = shift
            day_info["rotation_week"] = rotation_week
            day_info["hours"] = hours
            day_info["start"] = start
            day_info["end"] = end
            day_info["ob"] = ob

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

def summarize_month_for_person(year: int, month: int, person_id: int):
    days = generate_year_data(year, person_id)
    special_rules = build_special_ob_rules_for_year(year)
    combined_rules = ob_rules + special_rules
    totals = {
        "total_hours": 0.0,
        "num_shifts": 0,
        "ob_hours": {},
        "ob_pay": {},
        "brutto_pay": settings.monthly_salary,
    }
    days_out = []
    for day in days:
        if day["date"].month != month:
            continue
        hours = day.get("hours", 0.0)
        shift = day.get("shift")
        start = day.get("start")
        end = day.get("end")
        ob_hours = day.get("ob", {}) or {}
        if start and end:
            ob_pay = calculate_ob_pay(start, end, combined_rules, settings.monthly_salary)
        else:
            ob_pay = {r.code: 0.0 for r in combined_rules}

        totals["total_hours"] += hours
        if shift and not shift.code == "OFF":
            totals["num_shifts"] += 1

        for code, h in ob_hours.items():
            totals["ob_hours"][code] = totals["ob_hours"].get(code, 0.0) + h
        for code, p in ob_pay.items():
            totals["ob_pay"][code] = totals["ob_pay"].get(code, 0.0) + p
            totals["brutto_pay"] += p
        
        

        days_out.append({
            "date": day["date"],
            "weekday_name": day["weekday_name"],
            "shift": shift,
            "rotation_week": day.get("rotation_week"),
            "hours": hours,
            "ob_hours": ob_hours,
            "ob_pay": ob_pay,
            "start": start,
            "end": end,
        })
    
    netto_pay = totals["brutto_pay"] - calculate_tax_bracket(totals["brutto_pay"], tax_brackets)
    return {
        'year': year, 'month': month, 'person_id': person_id,
        'total_hours': totals['total_hours'],
        'num_shifts': totals['num_shifts'],
        'ob_hours': totals['ob_hours'],
        'ob_pay': totals['ob_pay'],
        'brutto_pay': totals['brutto_pay'],
        'netto_pay': netto_pay,
        'days': days_out
    }

def calculate_ob_hours(start_dt: datetime.datetime, end_dt: datetime.datetime, ob_rules: list):
    ob_totals = {}
    for rule in ob_rules:
        if rule.code not in ob_totals:
            ob_totals[rule.code] = 0.0

    # If shift has no start or end (e.g. OFF), there are no OB hours.
    if start_dt is None or end_dt is None:
        return ob_totals

    if end_dt <= start_dt:
        return ob_totals
    
    current_start = start_dt
    
    while current_start < end_dt:
        next_day = current_start.date() + datetime.timedelta(days=1)
        day_end = datetime.datetime.combine(
            next_day,
            datetime.time(0, 0)
        )
    
        segment_end = end_dt if end_dt <= day_end else day_end

        weekday = current_start.weekday()

        # Select rules that apply for the current date. Rules can specify
        # either `days` (weekday indexes) or `specific_dates` (ISO strings).
        date_iso = current_start.date().isoformat()
        todays_rules = []
        for rule in ob_rules:
            match = False
            # If rule defines weekdays, match by weekday
            if getattr(rule, 'days', None):
                try:
                    if weekday in rule.days:
                        match = True
                except TypeError:
                    # rule.days might be None or not iterable; skip
                    match = False
            # If rule defines specific_dates, match by exact date string
            if not match and getattr(rule, 'specific_dates', None):
                if date_iso in rule.specific_dates:
                    match = True
            if match:
                todays_rules.append(rule)

        # If special-holiday rules match, prefer OB5 over OB4; otherwise if
        # OB4 exists use OB4. This prevents double-counting (e.g. OB5 vs OB2)
        if any(r.code == 'OB5' for r in todays_rules):
            todays_rules = [r for r in todays_rules if r.code == 'OB5']
        elif any(r.code == 'OB4' for r in todays_rules):
            todays_rules = [r for r in todays_rules if r.code == 'OB4']

        # Allocate overlapping time to the highest-priority applicable rule.
        # Priority: OB5 > OB4 > others.
        def rule_priority(r):
            if r.code == 'OB5':
                return 3
            if r.code == 'OB4':
                return 2
            return 1

        # Sort todays_rules by descending priority so higher-priority rules
        # claim time first.
        rules_by_priority = sorted(todays_rules, key=rule_priority, reverse=True)

        # Track covered intervals (list of (start, end)) to avoid double-counting.
        covered: list[tuple[datetime.datetime, datetime.datetime]] = []

        for rule in rules_by_priority:
            start_h, start_m = map(int, rule.start_time.split(":"))
            end_h, end_m = map(int, rule.end_time.split(":"))

            ob_start = datetime.datetime(
                current_start.year,
                current_start.month,
                current_start.day,
                start_h,
                start_m
            )

            if rule.end_time == "24:00":
                ob_end = datetime.datetime(
                    current_start.year,
                    current_start.month,
                    current_start.day,
                    0,
                    0,
                ) + datetime.timedelta(days=1)
            else:
                ob_end = datetime.datetime(
                    current_start.year,
                    current_start.month,
                    current_start.day,
                    end_h,
                    end_m
                )

            overlap_start = max(current_start, ob_start)
            overlap_end = min(segment_end, ob_end)

            if overlap_end <= overlap_start:
                continue

            # Subtract already-covered intervals from this overlap.
            to_process = [(overlap_start, overlap_end)]
            new_intervals: list[tuple[datetime.datetime, datetime.datetime]] = []
            for seg_start, seg_end in to_process:
                cursor = seg_start
                # Subtract each covered interval
                for cov_start, cov_end in covered:
                    if cov_end <= cursor or cov_start >= seg_end:
                        continue
                    if cov_start > cursor:
                        new_intervals.append((cursor, min(cov_start, seg_end)))
                    cursor = max(cursor, cov_end)
                    if cursor >= seg_end:
                        break
                if cursor < seg_end:
                    new_intervals.append((cursor, seg_end))

            # Add uncovered pieces to totals and mark them covered.
            for ustart, uend in new_intervals:
                hours = (uend - ustart).total_seconds() / 3600.0
                ob_totals[rule.code] = ob_totals.get(rule.code, 0.0) + hours
                covered.append((ustart, uend))
            
        current_start = segment_end
    return ob_totals


def calculate_ob_pay(start_dt: datetime.datetime, end_dt: datetime.datetime, ob_rules: list, monthly_salary: int) -> dict:
    """Return a dict with OB pay per code based on hours and rule rate.

    hourly rate for a rule is computed as monthly_salary / rule.rate (as in
    the project notes). The function reuses calculate_ob_hours to get hours
    then multiplies by the hourly rate.
    """
    hours = calculate_ob_hours(start_dt, end_dt, ob_rules)
    pays = {}
    for rule in ob_rules:
        code = rule.code
        # skip rules with zero hours or missing rate
        h = hours.get(code, 0.0)
        try:
            rate_divisor = getattr(rule, 'rate', None)
            if rate_divisor and h > 0:
                hourly = monthly_salary / float(rate_divisor)
                pays[code] = h * hourly
            else:
                pays[code] = 0.0
        except Exception:
            pays[code] = 0.0
    return pays

def build_special_ob_rules_for_year(year: int) -> list[ObRule]:
    rules: list[ObRule] = []

    def add_interval(code: str, label: str, start_date: datetime.date,
                     start_time:str, rate: int):
        end_first_weekday = first_weekday_after(start_date)
        day = start_date
        first = True
        while day < end_first_weekday:
            if first:
                st = start_time
            else:
                st = "00:00"
            et = "24:00"
            rules.append(
                ObRule(
                    code=code,
                    label=label,
                    specific_dates=[day.isoformat()],
                    start_time=st,
                    end_time=et,
                    rate=rate
                )
            )
            first = False
            day += datetime.timedelta(days=1)
    add_interval("OB4", "Storhelg 300",
                 trettondagen(year), "07:00", 300)
    add_interval("OB4", "Storhelg 300",
                 forsta_maj(year), "07:00", 300)
    add_interval("OB4", "Storhelg 300",
                 nationaldagen(year), "07:00", 300)
    add_interval("OB4", "Storhelg 300",
                 kristi_himmelsfardsdag(year), "07:00", 300)
    add_interval("OB4", "Storhelg 300",
                 alla_helgons_dag(year), "07:00", 300)

    # New Year's Day was previously added as OB4; to ensure NYE (nyarsafton)
    # takes precedence (OB5) over Jan 1 we do not force Jan 1 to OB4 here.
    # Instead, add Jan 1 as OB5 (the nyarsafton interval below covers Dec 31)
    # so the evening and following early hours are consistently OB5.

    # OB5: from 18 on Skärtorsdagen and Nyårsafton
    add_interval("OB5", "Storhelg 150",
                 skartorsdagen(year), "18:00", 150)
    add_interval("OB5", "Storhelg 150",
                 nyarsafton(year), "18:00", 150)

    # Ensure the day after nyarsafton (new year's day) is also treated as OB5
    # for the full day so that NYE evening and the following early hours are
    # all OB5 (no split into OB2/OB4). This creates a specific-date rule for
    # Jan 1 with full-day 00:00-24:00 OB5.
    rules.append(
        ObRule(
            code="OB5",
            label="Storhelg 150",
            specific_dates=[(nyarsafton(year) + datetime.timedelta(days=1)).isoformat()],
            start_time="00:00",
            end_time="24:00",
            rate=150,
        )
    )

    # OB5: Good Friday (Långfredagen) is part of the full Easter weekend.
    # Add langfredagen with a 00:00 start so the rule covers Fri-Sat-Sun
    # (add_interval uses `first_weekday_after` which for a Friday returns
    # the next Monday, causing the loop to include Fri, Sat and Sun).
    add_interval("OB5", "Storhelg 150",
                 langfredagen(year), "00:00", 150)

    # OB5: from 07 on pingst-, midsommar- och julafton
    add_interval("OB5", "Storhelg 150",
                 pingstafton(year), "07:00", 150)
    add_interval("OB5", "Storhelg 150",
                 midsommarafton(year), "07:00", 150)
    add_interval("OB5", "Storhelg 150",
                 julafton(year), "07:00", 150)

    return rules

if __name__ == "__main__":
    import datetime
    
    result = generate_year_data(2026, 1)
    print(result)
    
    # test_date = datetime.date(2026, 12, 31)  # kolla i rotationen så du vet att det är jobb
    # for i in range(40):
    #     print("-", end="")
    # print("-")
    # for i in range(10):
    #     ob_hours = {}
    #     shift, _ = determine_shift_for_date(test_date, start_week=i+1)
    #     hours, start, end = calculate_shift_hours(test_date, shift)

    #     print("Pass:", shift.code, start, "->", end, "totalt", hours, "timmar")
    #     year = test_date.year
    #     special_rules = build_special_ob_rules_for_year(year)
    #     # Do not mutate module-level `ob_rules` (avoid duplicating rules on
    #     # repeated runs). Create a local combined list for this calculation.
    #     combined = ob_rules + special_rules

    #     ob_hours = calculate_ob_hours(start, end, combined)
    #     print("OB timmar:", ob_hours)
    #     ob_pay = calculate_ob_pay(start, end, combined, settings.monthly_salary)
    #     print("OB betalning (sek):", {k: round(v, 2) for k, v in ob_pay.items()})
    #     for i in range(40):
    #         print("-", end="")
    #     print("-")

    # test_date = 2026 
    # results = generate_year_data(test_date, 1)
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
    #                 for i in range(10):
    #                     print("-", end="") 
    #                 print("-")

    # person_id = 1
    # person_ids = list(range(1, 2))
    # for i in range(10):
    #     print("-", end="") 
    # print("-")
    # for person_id in person_ids:
    #     summary = summarize_year_by_month(test_date, person_id)
    #     print(f"Person ID: {person_id}")
    #     for month in sorted(summary.keys()):
    #         data = summary[month]
    #         print(f"Månad {month}: {data['total_hours']} timmar, {data['num_shifts']} pass")
    #     for i in range(10):
    #         print("-", end="") 
    #     print("-")

