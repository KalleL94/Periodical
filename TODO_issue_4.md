# TODO: Issue #4 - Absence Tracking (Frånvaro)

## ✅ Completed

### 1. Database (app/database/database.py)
- [x] Added `AbsenceType` enum with values: SICK, VAB, LEAVE
- [x] Added `absence_type` column to Absence model (SQLEnum)
- [x] Created migration script `migrate_absence_add_type.py`

### 2. Configuration (data/shift_types.json)
- [x] Removed generic "ABS" type
- [x] Added SICK: "Sjuk" with red color (#ef4444)
- [x] Added VAB: "VAB" with orange color (#f97316)
- [x] Added LEAVE: "Ledigt" with purple color (#a855f7)

### 3. API Endpoints (app/routes/auth_routes.py)
- [x] Added `POST /absence/add` endpoint
  - Accepts date and absence_type from form
  - Validates absence_type is SICK, VAB, or LEAVE
  - Creates/updates Absence record for logged-in user
  - Redirects to day view
- [x] Added `POST /absence/{absence_id}/delete` endpoint
  - Validates user owns the absence (or is admin)
  - Deletes absence record
  - Redirects back to day view

### 4. Schedule Logic (app/core/schedule/period.py)
- [x] Updated `_build_person_day_basic()` to check for absences first
- [x] Updated `_populate_single_person_day()` to prioritize absences
- [x] Absences now override rotation shifts with correct colors

### 5. UI (app/templates/day.html)
- [x] Added absence section (only visible for own page when logged in)
- [x] Form with dropdown for absence types (Sjuk, VAB, Ledigt)
- [x] Shows existing absence with type and delete button
- [x] Informative text about each absence type

### 6. Day Route (app/routes/public.py)
- [x] Imports Absence model
- [x] Fetches absence for person+date
- [x] Passes absence data to template

### 7. Wage Deductions (app/core/schedule/wages.py) ✅
- [x] Added `calculate_absence_deduction()` function
  - Calculates deduction based on hourly wage (monthly_wage / 173.33)
  - SICK: 100% deduction on karensdag (first day), 20% after (employer pays 80%)
  - VAB: 100% deduction (compensation from Försäkringskassan, not employer)
  - LEAVE: 100% deduction (unpaid leave)
- [x] Added `get_shift_hours_for_date()` to determine shift hours (default 8.5)
- [x] Added `get_absence_deductions_for_month()` for monthly calculations
  - Tracks sick periods and karensdag logic (new period after 5 days gap)
  - Returns detailed breakdown: total deduction, hours, days by type

### 8. Summary Integration (app/core/schedule/summary.py) ✅
- [x] Integrated absence deductions in `summarize_month_for_person()`
- [x] Deductions subtracted from gross pay before tax calculation
- [x] Added absence statistics to monthly summaries:
  - absence_deduction: Total deduction amount
  - absence_hours: Total hours absent
  - sick_days, vab_days, leave_days: Count by type
- [x] Added absence totals to yearly summaries

## 📝 Next Steps

### 1. Run Migration
```bash
python migrate_absence_add_type.py
```

### 2. Test the Feature
- Login as a user
- Navigate to your day view
- Register an absence (try all three types)
- Verify colors in calendar: red (SICK), orange (VAB), purple (LEAVE)
- Test deleting an absence
- Check month view to verify wage deductions are applied
- Verify karensdag logic for sick leave (100% first day, 20% after)

## Notes
- Absence types now affect wages correctly:
  - SICK: Karensdag (100% deduction) then 80% sick pay from employer
  - VAB: No pay from employer (Försäkringskassan handles compensation)
  - LEAVE: Unpaid leave (100% deduction)
- Wage calculations use 173.33 hours/month (Swedish standard)
- Default shift is 8.5 hours if actual shift can't be determined
- Absence colors are distinct for easy visual identification in calendar views
