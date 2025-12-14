# Periodical - Architecture Documentation

## Project Overview

FastAPI web application for managing employee shift schedules and calculating Swedish OB (inconvenient hours) pay for a 10-person rotation system.

**Key Features:**
- 10-week rotation cycles (N1/N2/N3 shifts + OFF days)
- Swedish OB pay calculation (OB1-OB5 rules)
- Holiday handling (Easter, Midsummer, Christmas, etc.)
- Vacation tracking per person
- JWT-based authentication with role-based access control
- SQLite database persistence
- Mobile-responsive web interface

## Development Commands

**Run the application:**
```bash
uvicorn app.main:app --reload
```
Must run from project root so `data/*.json` files are accessible.

**Install dependencies:**
```bash
pip install -r requirements.txt
```

**Database migration (initial setup):**
```bash
python migrate_to_db.py
```
Creates SQLite database, imports persons from `data/persons.json`, and creates default accounts.

**Run tests:**
```bash
python tests/test_ob_calculation.py
# Or with pytest:
pytest tests/test_ob_calculation.py -v
```

## Git Workflow and Versioning

### Version Tags

This project uses git tags for versioning. Each significant state is tagged with a version number:

```bash
# View all version tags
git tag

# View commits with their tags
git log --oneline --decorate

# Checkout a specific version
git checkout v0.0.13
```

### Commit and Tag Convention

When creating new versions:

1. **Make your changes** to the codebase
2. **Stage the changes:**
   ```bash
   git add .
   ```
3. **Commit with descriptive message:**
   ```bash
   git commit -m "Brief description of what changed"
   ```
   - Use clear, concise messages
   - Focus on WHAT changed, not why
   - Examples: "Add user profile interface", "Fix OB calculation for holidays"

4. **Tag the commit** with version number:
   ```bash
   git tag v0.0.X
   ```
   - Use semantic versioning: `vMAJOR.MINOR.PATCH`
   - Current format: `v0.0.X` for incremental releases
   - Tags should be annotated for production releases:
     ```bash
     git tag -a v1.0.0 -m "Release version 1.0.0"
     ```

### Example Workflow

```bash
# Make changes to code
nano app/main.py

# Stage changes
git add app/main.py

# Commit
git commit -m "Improve authentication error handling"

# Tag this version
git tag v0.0.14

# View the result
git log --oneline --decorate -n 1
# Output: a1b2c3d (HEAD -> main, tag: v0.0.14) Improve authentication error handling
```

### Pushing to Remote

```bash
# Push commits
git push origin main

# Push tags (IMPORTANT - tags don't push automatically)
git push origin --tags

# Or push specific tag
git push origin v0.0.14
```

### Version History

Current versions in this repository:
- `v0.0.1` - Initial FastAPI application
- `v0.0.2` - All-team week view
- `v0.0.3` - OB rules and expanded week views
- `v0.0.4` - Swedish holiday calculations, person management, month/year views, test suite
- `v0.0.5` - (No code changes)
- `v0.0.6` - Detailed day view
- `v0.0.7` - Improved year statistics
- `v0.0.8` - (No code changes)
- `v0.0.9` - Core module refactoring
- `v0.0.10` - (No code changes)
- `v0.0.11` - JWT authentication and database
- `v0.0.12` - User profile and vacation interfaces
- `v0.0.13` - README and auth routing improvements

## System Architecture

### Core Components

1. **Application Entry** (`app/main.py`)
   - FastAPI app initialization
   - Static file mounting
   - Router registration
   - Database table creation

2. **Route Handlers** (`app/routes/`)
   - `public.py` - All schedule views (week, day, month, year)
   - `auth_routes.py` - Login/logout endpoints
   - `admin.py` - Admin settings management

3. **Schedule Engine** (`app/core/schedule.py`)
   - Rotation calculation algorithm
   - OB hours/pay calculation
   - Holiday handling
   - Vacation override logic

4. **Helper Functions** (`app/core/helpers.py`)
   - Permission checks (`can_see_salary`)
   - Salary data sanitization (`strip_salary_data`, `strip_year_summary`)
   - Template rendering helper (`render_template`)
   - Badge contrast color calculation (`contrast_color`)

5. **Data Models** (`app/core/models.py`)
   - Pydantic models for type safety
   - Person, ShiftType, Rotation, ObRule, Settings

6. **Data Storage** (`app/core/storage.py`)
   - JSON file loaders
   - All configuration stored in `data/` directory

7. **Authentication** (`app/auth/auth.py`)
   - JWT token generation/validation
   - Password hashing (bcrypt)
   - Role-based access control

8. **Database** (`app/database/database.py`)
   - SQLAlchemy + SQLite
   - User authentication persistence

### Data Flow

1. Application loads JSON configs at startup (`data/*.json`)
2. User authenticates → JWT token issued
3. Route handler fetches user from database
4. Schedule engine calculates shifts for requested period
5. OB calculator determines pay based on shift times + rules
6. Template renders data with role-appropriate visibility

### Key Algorithms

**Rotation Calculation:**
- 10-week cycle starting from `rotation_start_date`
- Each person offset by `start_week` (1-10)
- Formula: `((weeks_since_start + person.start_week - 1) % 10) + 1`

**OB Priority System:**
- OB5 (150% Storhelg) > OB4 (300kr Helgdag) > Others
- Prevents double-counting overlapping periods via `_subtract_covered_interval`

**Holiday OB Generation:**
- Programmatically generates OB4/OB5 blocks for Swedish holidays
- Extends through weekends per Swedish labor law
- Holidays: Easter, Midsummer, Christmas, New Year, etc.

## Configuration

All business logic is data-driven:

- `data/rotation.json` - 10-week rotation pattern
- `data/shift_types.json` - Shift definitions (N1/N2/N3/OFF/SEM)
- `data/settings.json` - Rotation start date, default salary
- `data/persons.json` - Team members, wages, vacation
- `data/ob_rules.json` - Base OB rules (evening, night, weekend)
- `data/tax_brackets.json` - Swedish tax brackets

### Critical Data Conventions

- Rotation weeks are **strings** ("1" through "10"), not integers
- Weekday arrays are 0-indexed: Monday (0) through Sunday (6)
- Times in "HH:MM" format; "24:00" handled specially as next-day midnight
- Person IDs are 1-based (1-10)
- Vacation data keyed by year string: `{"2026": [25, 26, 27]}`

## Security

- JWT tokens (HS256 algorithm)
- Bcrypt password hashing
- Role-based access: `user` vs `admin`
- Admin users see all wages, regular users see only their own
- Secure cookies (set `secure=True` for HTTPS in production)

**IMPORTANT:** Change `SECRET_KEY` in `app/auth/auth.py` before production deployment!

## Key Routes

**Authentication:**
- `GET /login` - Login page
- `POST /login` - Authenticate user
- `GET /logout` - Clear session

**Main Views:**
- `/week` - Current week view for all persons
- `/week/{person_id}` - Week view for specific person
- `/day/{person_id}/{year}/{month}/{day}` - Detailed day view with OB breakdown
- `/month/{person_id}` - Monthly summary
- `/year/{person_id}` - Yearly summary with co-working statistics

**Admin:**
- `/admin/settings` - Editable settings and person wages

## Working Directory Dependency

All data loaders use relative paths like `Path('data/rotation.json')`. The application **must** be run from the project root directory or data loading will fail.

## Performance Optimizations

- `@lru_cache` used extensively on pure functions
- `generate_year_data(year, person_id)` cached with maxsize=128
- Holiday rules cached per year
- Vacation dates calculated once per year
- Cache invalidation on module reload (after settings updates)

## Testing

**Comprehensive OB calculation tests** (`tests/test_ob_calculation.py`):
- Regular shift OB calculations (weekday, evening, night)
- Holiday OB rules (Good Friday, Christmas, New Year)
- OB priority system validation
- Edge cases (midnight spanning, weekend blocks)

Run tests:
```bash
pytest tests/test_ob_calculation.py -v
```

## Common Editing Patterns

**Add a new shift type:**
1. Add entry to `data/shift_types.json` with code, label, start_time, end_time, color
2. Reference the code in `data/rotation.json` week arrays

**Change rotation start date:**
Edit `rotation_start_date` in `data/settings.json` (ISO format "YYYY-MM-DD")

**Add vacation for a person:**
Edit `data/persons.json`:
```json
{
  "id": 1,
  "name": "Person 1",
  "wage": 37000,
  "vacation": {
    "2026": [25, 26, 27],
    "2027": [30, 31]
  }
}
```

**Modify OB rules:**
- Static rules: Edit `data/ob_rules.json`
- Holiday rules: Modify `build_special_ob_rules_for_year` in `schedule.py`

**Add a new holiday:**
1. Add function to `app/core/holidays.py` (follow existing patterns)
2. Call from `build_special_ob_rules_for_year` with appropriate parameters

## Known Gotchas

- Do not change rotation.weeks keys from strings to integers
- End time "24:00" requires special handling
- Person IDs are 1-based but used as array indices minus 1
- Vacation weeks use ISO week numbers (1-53)
- Templates expect exactly 10 persons
- Database file (`schedule.db`) should never be committed
- Default password from migration script is "London1" - must be changed

## Production Deployment

**Before deploying:**
1. Change `SECRET_KEY` in `app/auth/auth.py` (use environment variable)
2. Set `secure=True` for cookies (requires HTTPS)
3. Change all default passwords
4. Set up proper database backup strategy
5. Configure reverse proxy (nginx, traefik)
6. Use proper process manager (systemd, supervisor)
7. Set appropriate file permissions

**Development:**
```bash
uvicorn app.main:app --reload
```

**Production:**
Use a production ASGI server like Gunicorn with Uvicorn workers:
```bash
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker
```

## Language and Localization

- Swedish weekday names in `weekday_names` array
- Swedish holiday calculations (påsk, midsommar, jul)
- UI labels in Swedish
- Date format: ISO 8601 throughout
