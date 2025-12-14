# Periodical

Swedish employee shift scheduling and OB (inconvenient hours) pay calculation system for a 10-person rotating team.

## Features

### Core Functionality
- **10-week rotation schedule** - N1 (Day), N2 (Evening), N3 (Night) shifts with automatic rotation
- **OB calculation** - Swedish labor law compliant (OB1-OB5: evening, night, weekend, holiday, major holiday)
- **Holiday handling** - Automatic calculation for Swedish holidays (Easter, Midsummer, Christmas, etc.)
- **On-call shifts** - OC (Beredskap) with pay calculation
- **Overtime tracking** - OT shifts with database persistence
- **Vacation management** - Per-user vacation tracking by ISO week number

### Views
- **Week view** - Individual or all-team weekly schedule
- **Month view** - Calendar grid layout with ISO week numbers (Mondays) and rotation weeks (Sundays)
- **Day view** - Detailed breakdown with OB hours/pay, on-call, and overtime
- **Year view** - Annual summary with monthly breakdown and co-working statistics

### Security & Access
- **JWT authentication** - Secure token-based auth with bcrypt password hashing
- **Role-based access control** - Admin sees all salaries, users see only their own
- **Password change on first login** - Forced password update for new accounts
- **CORS configuration** - Environment-based restrictions for production
- **File permissions setup** - Secure ownership and modes for production

### Production Features
- **Structured logging** - JSON logs with request IDs and performance tracking
- **Sentry error tracking** - Optional production error monitoring (requires sentry-sdk[fastapi])
- **Environment variables** - Secure configuration via .env file
- **Docker support** - Complete containerization with docker-compose
- **Database backups** - Automated backup and restore scripts
- **HTTPS deployment** - Nginx/Traefik reverse proxy configurations

## Quick Start

```bash
# Clone repository
git clone git@github.com:KalleL94/Periodical.git
cd Periodical

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Run database migration (creates DB and default users)
python migrate_to_db.py

# Start development server
uvicorn app.main:app --reload
```

Application runs at: http://localhost:8000

## Default Users

After migration, these accounts are created (default password: `London1`):

| Username | Role  | Person | Description |
|----------|-------|--------|-------------|
| admin    | Admin | -      | Full access, can manage all users |
| ddf412   | User  | ID 6   | Kalle |
| ...      | User  | ID 1-10| Team members |

**⚠️ Change passwords immediately after first login!**

## Configuration

### Environment Variables

Create `.env` file in project root:

```bash
# Copy example file
cp .env.example .env
```

Required variables:
```bash
# SECRET_KEY - JWT token signing (CRITICAL!)
# Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
SECRET_KEY=your-secret-key-here

# PRODUCTION - Set to 'true' in production
PRODUCTION=true

# CORS_ORIGINS - Comma-separated allowed origins (required in production)
CORS_ORIGINS=https://your-domain.com,https://www.your-domain.com

# SENTRY_DSN - Optional error tracking (get from sentry.io)
# SENTRY_DSN=https://abc123@o123456.ingest.sentry.io/7654321

# SENTRY_ENVIRONMENT - Optional (defaults to "production")
# SENTRY_ENVIRONMENT=production

# RELEASE_VERSION - Optional version tracking
# RELEASE_VERSION=periodical@0.0.20

# Database URL (optional, defaults to SQLite)
# DATABASE_URL=sqlite:///./app/database/schedule.db

# Logging level (optional, defaults to INFO)
# LOG_LEVEL=INFO

# Timezone (optional, defaults to UTC)
TZ=Europe/Stockholm
```

### Data Configuration

All business logic is data-driven via JSON files in `data/`:

- `persons.json` - Team members, wages, vacation weeks
- `rotation.json` - 10-week rotation pattern
- `shift_types.json` - Shift definitions (N1/N2/N3/OFF/SEM/OC/OT)
- `settings.json` - Rotation start date, default salary
- `ob_rules.json` - Base OB rules (evening, night, weekend)
- `oncall_rules.json` - On-call pay calculation rules
- `tax_brackets.json` - Swedish tax brackets

## Project Structure

```
Periodical/
├── app/
│   ├── auth/              # JWT authentication, password hashing
│   ├── core/              # Business logic
│   │   ├── schedule.py    # Rotation & OB calculation
│   │   ├── oncall.py      # On-call pay calculation
│   │   ├── holidays.py    # Swedish holiday calculations
│   │   ├── config.py      # Constants and configuration
│   │   ├── logging_config.py    # Structured logging setup
│   │   ├── sentry_config.py     # Sentry error tracking
│   │   ├── request_logging.py   # Request/response logging
│   │   └── helpers.py     # Utility functions
│   ├── database/          # SQLAlchemy models (User, OvertimeShift)
│   ├── routes/            # FastAPI routes
│   │   ├── public.py      # Schedule views
│   │   ├── auth_routes.py # Login/logout/password change
│   │   └── admin.py       # Admin settings
│   ├── static/            # CSS (style.css with calendar grid)
│   └── templates/         # Jinja2 HTML templates
├── data/                  # JSON configuration files
├── deployment/            # Docker, nginx, systemd configs
├── docs/                  # Documentation (CORS, Logging, Sentry, Permissions)
├── scripts/               # Backup, restore, permissions setup
├── migrate_to_db.py       # Initial database setup
├── migrate_add_password_change.py  # Add password change tracking
├── requirements.txt       # Python dependencies
├── ARCHITECTURE.md        # Detailed architecture documentation
├── DEPLOYMENT.md          # Production deployment guide
└── .env.example           # Environment variables template
```

## Production Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for complete production setup guide.

### Quick Production Setup

```bash
# 1. Setup environment and permissions
./scripts/setup_production.sh

# 2. Configure environment variables
cp .env.example .env
nano .env  # Edit SECRET_KEY, CORS_ORIGINS, SENTRY_DSN

# 3. Run with systemd service
sudo cp deployment/ica-schedule.service /etc/systemd/system/
sudo systemctl enable ica-schedule
sudo systemctl start ica-schedule

# 4. Setup nginx reverse proxy (see deployment/nginx-example.conf)
```

### Docker Deployment

```bash
# Build and run with docker-compose
docker-compose -f deployment/docker-compose.yml up -d

# View logs
docker-compose -f deployment/docker-compose.yml logs -f
```

## Database Backups

```bash
# Backup database
./scripts/backup_database.sh

# Restore from backup
./scripts/restore_database.sh backups/schedule_YYYYMMDD_HHMMSS.db.gz
```

Backups are stored in `backups/` directory (excluded from git).

## Development

### Running Tests

```bash
# Run OB calculation tests
pytest tests/test_ob_calculation.py -v

# Or with Python directly
python tests/test_ob_calculation.py
```

### Key Routes

**Authentication:**
- `GET /login` - Login page
- `POST /login` - Authenticate user
- `GET /logout` - Clear session
- `GET /profile` - User profile and vacation management
- `POST /profile/password` - Change password

**Schedule Views:**
- `GET /week/{person_id}` - Individual week view
- `GET /week` - All-team week view
- `GET /month/{person_id}` - Individual month calendar grid
- `GET /month` - All-team month view
- `GET /day/{person_id}/{year}/{month}/{day}` - Detailed day view
- `GET /year/{person_id}` - Individual year summary
- `GET /year` - All-team year view

**Admin:**
- `GET /admin/settings` - Edit settings and person wages
- `GET /admin/users` - User management
- `GET /admin/rotation` - Edit rotation pattern
- `GET /admin/shift-types` - Edit shift types

**API:**
- `GET /api/year/{year}/totals/{person_id}` - JSON lazy-loading for year totals

## Tech Stack

- **Framework:** FastAPI (async Python web framework)
- **Database:** SQLAlchemy + SQLite (or PostgreSQL via DATABASE_URL)
- **Templates:** Jinja2 with responsive CSS
- **Authentication:** JWT tokens with bcrypt password hashing
- **Logging:** Structured JSON logging with request IDs
- **Error Tracking:** Sentry (optional, production)
- **Deployment:** Docker, systemd, nginx/Traefik
- **Testing:** pytest

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) - Detailed architecture and algorithms
- [DEPLOYMENT.md](DEPLOYMENT.md) - Production deployment guide
- [docs/CORS.md](docs/CORS.md) - CORS configuration
- [docs/LOGGING.md](docs/LOGGING.md) - Structured logging
- [docs/SENTRY.md](docs/SENTRY.md) - Error tracking setup
- [docs/FILE_PERMISSIONS.md](docs/FILE_PERMISSIONS.md) - Security permissions

## Version History

See git tags for version history:
```bash
git tag
git log --oneline --decorate
```

Current version: **v0.0.20+**

## License

Private project - All rights reserved.

## Author

Kalle L - [@KalleL94](https://github.com/KalleL94)
