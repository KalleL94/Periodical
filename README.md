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
- **ICS calendar export** - Export the next 6 months of schedule to calendar applications

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
- **File permissions setup** - Secure ownership and modes for production deployment

### Production Features
- **Structured logging** - JSON logs with request IDs and performance tracking
- **Sentry error tracking** - Optional production error monitoring
- **Environment variables** - Secure configuration via .env file
- **Docker support** - Complete containerization with docker-compose
- **Database backups** - Automated backup and restore scripts
- **HTTPS deployment** - Nginx/Traefik reverse proxy configurations
- **Health check endpoint** - `/health` for monitoring and load balancers
- **CI/CD pipeline** - Automated testing and deployment via GitHub Actions

## Quick Start

### Prerequisites

- **Python 3.11 or later** (project developed with Python 3.12)
- Git
- Virtual environment (recommended)

### Installation

```bash
# Clone repository
git clone git@github.com:KalleL94/Periodical.git
cd Periodical

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# Install dependencies (from pyproject.toml)
pip install .

# For development (includes pytest, ruff, etc.)
pip install ".[dev]"

# Run database migration (creates DB and default users)
python migrate_to_db.py
python migrate_add_password_change.py

# Start development server
uvicorn app.main:app --reload
```

Application runs at: http://localhost:8000

## Default Users

After migration, these accounts are created:

| Username | Role  | Person | Default Password | Description |
|----------|-------|--------|------------------|-------------|
| admin    | Admin | -      | Banan1          | Full access, can manage all users |
| ddf412   | User  | ID 6   | London1         | Kalle |
| ...      | User  | ID 1-10| London1         | Team members |

**⚠️ Change passwords immediately after first login!**

The system will force password change on first login for security.

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
PRODUCTION=false

# CORS_ORIGINS - Comma-separated allowed origins (required in production)
# CORS_ORIGINS=https://your-domain.com,https://www.your-domain.com

# SENTRY_DSN - Optional error tracking (get from sentry.io)
# SENTRY_DSN=https://abc123@o123456.ingest.sentry.io/7654321

# SENTRY_ENVIRONMENT - Optional (defaults to "production")
# SENTRY_ENVIRONMENT=production

# RELEASE_VERSION - Optional version tracking
# RELEASE_VERSION=periodical@1.0.0

# Database URL (optional, defaults to SQLite)
# DATABASE_URL=sqlite:///./app/database/schedule.db

# Logging level (optional, defaults to INFO)
# LOG_LEVEL=INFO

# Timezone (optional, defaults to UTC)
TZ=Europe/Stockholm
```

### Timezone Handling

Periodical uses explicit timezone management to ensure consistent "today" calculations regardless of server timezone settings:

- **Application timezone**: Europe/Stockholm (hardcoded in `app/core/utils.py`)
- **All "today" calculations**: Use `get_today()` helper function (Stockholm time)
- **Database timestamps**: Use UTC via `datetime.utcnow()` (JWT tokens, logging, created_at fields)
- **Performance timing**: Uses local `datetime.now()` (timing only, not business logic)

This ensures:
- Today highlighting works correctly in calendars
- Vacation week calculations use Stockholm time
- Current shift detection is accurate even around midnight
- No timezone-related bugs at DST transitions

**Note:** While `TZ` environment variable can be set, the application explicitly uses Stockholm time for all date-based business logic to prevent timezone-related bugs.

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
├── .github/
│   └── workflows/              # CI/CD pipelines
│       ├── ci.yml              # Automated testing on PRs
│       └── deploy.yml          # Automated deployment to production
├── app/
│   ├── auth/                   # JWT authentication, password hashing
│   ├── core/                   # Business logic
│   │   ├── schedule/           # Modular schedule system
│   │   │   ├── core.py         # Core rotation logic
│   │   │   ├── ob.py           # OB calculation
│   │   │   ├── overtime.py     # Overtime tracking
│   │   │   ├── vacation.py     # Vacation management
│   │   │   ├── wages.py        # Wage calculations
│   │   │   ├── period.py       # Period data generation
│   │   │   ├── summary.py      # Summary calculations
│   │   │   ├── cowork.py       # Co-working statistics
│   │   │   └── holidays_ob.py  # Holiday OB rules
│   │   ├── calendar_export.py  # ICS calendar generation
│   │   ├── holidays.py         # Swedish holiday calculations
│   │   ├── config.py           # Constants and configuration
│   │   ├── logging_config.py   # Structured logging setup
│   │   ├── sentry_config.py    # Sentry error tracking
│   │   ├── request_logging.py  # Request/response logging middleware
│   │   ├── storage.py          # JSON data loaders
│   │   ├── models.py           # Pydantic data models
│   │   ├── validators.py       # Input validation
│   │   └── helpers.py          # Utility functions
│   ├── database/               # SQLAlchemy models (User, OvertimeShift)
│   ├── routes/                 # FastAPI routes
│   │   ├── public.py           # Schedule views
│   │   ├── auth_routes.py      # Login/logout/password change
│   │   └── admin.py            # Admin settings
│   ├── static/                 # Static assets
│   │   └── css/                # Modular CSS files (base, calendar, components, layout, navigation, tables)
│   └── templates/              # Jinja2 HTML templates
├── data/                       # JSON configuration files
├── deployment/                 # Docker, nginx, systemd configs
│   ├── docker-compose.yml      # Docker deployment
│   ├── Dockerfile              # Container image
│   ├── nginx-example.conf      # Nginx reverse proxy config
│   ├── traefik.yml             # Traefik reverse proxy config
│   ├── ica-schedule.service    # Systemd service file
│   └── README.md               # Deployment documentation
├── docs/                       # Additional documentation
│   ├── CORS.md                 # CORS configuration guide
│   ├── LOGGING.md              # Logging setup and usage
│   ├── SENTRY.md               # Error tracking setup
│   └── FILE_PERMISSIONS.md     # Security permissions guide
├── scripts/                    # Utility scripts
│   ├── backup_database.sh      # Database backup automation
│   ├── restore_database.sh     # Database restore
│   ├── deploy.sh               # Production deployment script
│   ├── set_permissions.sh      # File permissions setup
│   └── setup_production.sh     # Complete production setup
├── tests/                      # Test suite
│   ├── conftest.py             # Pytest fixtures
│   ├── test_api.py             # API integration tests
│   ├── test_ob_calculation.py  # OB calculation tests
│   ├── test_calendar_export.py # Calendar export tests
│   └── test_rotation.py        # Rotation logic tests
├── migrate_to_db.py            # Initial database setup
├── migrate_add_password_change.py  # Password change migration
├── migrate_overtime.py         # Overtime migration
├── pyproject.toml              # Project metadata and dependencies
├── requirements.txt            # Pinned dependencies (auto-generated)
├── .pre-commit-config.yaml     # Pre-commit hooks configuration
├── ARCHITECTURE.md             # Detailed architecture documentation
├── DEPLOYMENT.md               # Production deployment guide
└── README.md                   # This file
```

## Production Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for complete production setup guide including:
- HTTPS configuration with Let's Encrypt
- Nginx/Traefik reverse proxy setup
- Systemd service configuration
- Docker deployment
- Database backups
- Monitoring and logging
- CI/CD with GitHub Actions

### Quick Production Setup

```bash
# 1. Setup environment and permissions
./scripts/setup_production.sh

# 2. Configure environment variables
cp .env.example .env
nano .env  # Edit SECRET_KEY, CORS_ORIGINS, SENTRY_DSN

# 3. Run migrations
python migrate_to_db.py
python migrate_add_password_change.py

# 4. Run with systemd service
sudo cp deployment/ica-schedule.service /etc/systemd/system/
sudo systemctl enable ica-schedule
sudo systemctl start ica-schedule

# 5. Setup nginx reverse proxy (see deployment/nginx-example.conf)
sudo cp deployment/nginx-example.conf /etc/nginx/sites-available/ica-schedule
sudo ln -s /etc/nginx/sites-available/ica-schedule /etc/nginx/sites-enabled/
sudo certbot --nginx -d your-domain.com
sudo systemctl restart nginx
```

### Docker Deployment

```bash
# Build and run with docker-compose
docker-compose -f deployment/docker-compose.yml up -d

# View logs
docker-compose -f deployment/docker-compose.yml logs -f
```

### CI/CD Pipeline

Periodical includes automated CI/CD via GitHub Actions:

**Continuous Integration (`.github/workflows/ci.yml`):**
- Triggers on Pull Requests to `main`
- Runs syntax checks and pytest
- Prevents buggy code from being merged

**Continuous Deployment (`.github/workflows/deploy.yml`):**
- Triggers on push/merge to `main`
- Automatically deploys to production via SSH
- Runs health checks to verify deployment

**Setup GitHub Actions:**
1. Configure secrets in GitHub repository settings:
   - `PROD_HOST` - Production server IP/domain
   - `PROD_USER` - SSH user (e.g., `deploy`)
   - `PROD_SSH_KEY` - Private SSH key for authentication
   - `PROD_APP_PATH` - Application path (e.g., `/opt/Periodical`)

2. See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed CI/CD setup instructions

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
# Run all tests
pytest

# Run specific test file
pytest tests/test_ob_calculation.py -v

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test class
pytest tests/test_api.py::TestAuthenticationFlow -v
```

### Code Quality

```bash
# Install pre-commit hooks
pre-commit install

# Run linting manually
ruff check .

# Format code
ruff format .

# Type checking (if using mypy)
mypy app/
```

### Key Routes

**Authentication:**
- `GET /login` - Login page
- `POST /login` - Authenticate user
- `GET /logout` - Clear session
- `GET /profile` - User profile page
- `POST /profile` - Update user profile
- `POST /profile/password` - Change password
- `GET /profile/vacation` - Vacation management page
- `POST /profile/vacation` - Update vacation weeks

**Schedule Views:**
- `GET /` - Dashboard (redirects to week view)
- `GET /week/{person_id}` - Individual week view
- `GET /week` - All-team week view
- `GET /month/{person_id}` - Individual month calendar grid
- `GET /month` - All-team month view
- `GET /day/{person_id}/{year}/{month}/{day}` - Detailed day view
- `GET /year/{person_id}` - Individual year summary
- `GET /year` - All-team year view

**Calendar Export:**
- `GET /profile/calendar.ics/{lang}` - Download ICS calendar file (6 months)

**Admin:**
- `GET /admin/settings` - Edit settings and person wages
- `GET /admin/users` - User management
- `GET /admin/users/create` - Create new user
- `GET /admin/users/{user_id}` - Edit user
- `POST /admin/users/{user_id}/reset-password` - Reset user password

**Overtime Management:**
- `POST /overtime/add` - Add overtime shift
- `POST /overtime/{ot_id}/delete` - Delete overtime shift

**API:**
- `GET /api/year/{year}/totals/{person_id}` - JSON lazy-loading for year totals
- `GET /health` - Health check endpoint (for monitoring)

## Tech Stack

- **Framework:** FastAPI (async Python web framework)
- **Database:** SQLAlchemy + SQLite (or PostgreSQL via DATABASE_URL)
- **Templates:** Jinja2 with responsive CSS
- **Authentication:** JWT tokens with bcrypt password hashing
- **Logging:** Structured JSON logging with request IDs
- **Error Tracking:** Sentry (optional, production)
- **Deployment:** Docker, systemd, nginx/Traefik
- **Testing:** pytest with fixtures and coverage
- **CI/CD:** GitHub Actions
- **Code Quality:** Ruff (linting and formatting), pre-commit hooks

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) - Detailed architecture and algorithms
- [DEPLOYMENT.md](DEPLOYMENT.md) - Production deployment guide with CI/CD setup
- [docs/CORS.md](docs/CORS.md) - CORS configuration for production
- [docs/LOGGING.md](docs/LOGGING.md) - Structured logging setup and usage
- [docs/SENTRY.md](docs/SENTRY.md) - Error tracking setup with Sentry
- [docs/FILE_PERMISSIONS.md](docs/FILE_PERMISSIONS.md) - Security permissions guide

## Security Best Practices

### Before Production Deployment

- [ ] Generate secure `SECRET_KEY` (use `python -c "import secrets; print(secrets.token_urlsafe(32))"`)
- [ ] Set `PRODUCTION=true` in environment variables
- [ ] Configure `CORS_ORIGINS` with your actual domain(s)
- [ ] Change all default passwords (admin: Banan1, users: London1)
- [ ] Enable HTTPS with valid SSL certificate
- [ ] Set up file permissions using `./scripts/set_permissions.sh`
- [ ] Configure firewall (only ports 80, 443 open)
- [ ] Enable Sentry error tracking (optional but recommended)
- [ ] Set up automated database backups
- [ ] Configure log rotation
- [ ] Review and restrict admin access

### CORS Configuration

Periodical automatically configures CORS based on the `PRODUCTION` environment variable:

- **Development (`PRODUCTION=false`)**: Permissive CORS for easy testing
- **Production (`PRODUCTION=true`)**: Strict CORS - only specified origins allowed

See [docs/CORS.md](docs/CORS.md) for detailed configuration.

### File Permissions

For production deployments, use the provided script to set secure permissions:

```bash
./scripts/set_permissions.sh
```

This ensures:
- Application files are owned by the correct user
- Database has appropriate read/write permissions
- Logs directory is writable
- Configuration files are protected

See [docs/FILE_PERMISSIONS.md](docs/FILE_PERMISSIONS.md) for details.

## Monitoring and Logging

### Structured Logging

Periodical uses structured JSON logging in production with:
- Request IDs for tracing
- User context in logs
- Performance metrics (request duration)
- Authentication event logging
- Automatic log rotation

See [docs/LOGGING.md](docs/LOGGING.md) for complete logging guide.

### Error Tracking

Optional Sentry integration for production error monitoring:

```bash
# Install Sentry SDK (already included in dependencies)
pip install sentry-sdk[fastapi]

# Configure in .env
SENTRY_DSN=https://your-sentry-dsn@sentry.io/project-id
SENTRY_ENVIRONMENT=production
RELEASE_VERSION=periodical@1.0.0
```

See [docs/SENTRY.md](docs/SENTRY.md) for setup instructions.

### Health Checks

The `/health` endpoint provides application status for monitoring:

```bash
curl http://localhost:8000/health
# Response: {"status":"healthy","service":"periodical","version":"1.0.0"}
```

Use this endpoint with:
- Load balancers
- Uptime monitoring services (UptimeRobot, Better Uptime)
- Kubernetes liveness/readiness probes
- Docker health checks

## Version History

See git tags for version history:
```bash
git tag
git log --oneline --decorate
```

**Current version: 1.0.0**

Major releases:
- **v1.0.0** - Production-ready release with CI/CD, structured logging, and comprehensive documentation
- **v0.0.13** - README and auth routing improvements
- **v0.0.12** - User profile and vacation interfaces
- **v0.0.11** - JWT authentication and database
- **v0.0.9** - Core module refactoring
- **v0.0.4** - Swedish holiday calculations, test suite
- **v0.0.1** - Initial FastAPI application

## Troubleshooting

### Common Issues

**Database locked errors:**
- SQLite has limited concurrency
- Use `--workers 1` for uvicorn
- Consider PostgreSQL for high-traffic deployments

**Import errors after updates:**
```bash
# Reinstall dependencies
pip install --upgrade .
```

**Permission denied errors:**
```bash
# Fix file permissions
./scripts/set_permissions.sh
```

**CORS errors in production:**
- Ensure `CORS_ORIGINS` is set in `.env`
- Check that your domain is included in the list
- See [docs/CORS.md](docs/CORS.md)

**Health check fails:**
```bash
# Check application status
sudo systemctl status ica-schedule

# View logs
sudo journalctl -u ica-schedule -n 50
```

### Getting Help

1. Check [ARCHITECTURE.md](ARCHITECTURE.md) for technical details
2. Review [DEPLOYMENT.md](DEPLOYMENT.md) for deployment issues
3. Check relevant docs in `docs/` directory
4. Review application logs in `logs/` directory
5. Create an issue on GitHub

## Contributing

This is a private project, but contributions are welcome:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests (`pytest`)
5. Run linting (`ruff check .`)
6. Commit your changes (`git commit -m 'Add amazing feature'`)
7. Push to the branch (`git push origin feature/amazing-feature`)
8. Open a Pull Request

The CI pipeline will automatically run tests on your PR.

## License

Private project - All rights reserved.

## Author

Kalle L - [@KalleL94](https://github.com/KalleL94)

---

**Built with ❤️ for efficient shift scheduling and accurate OB calculations**
