# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries here are written in English; this file documents the project for
developers. User-facing release notes are separate and bilingual: they live in
`VERSIONS` in `app/routes/changelog.py`, which renders in the language the user
has selected. Add both when a change is worth telling users about.

## [Unreleased]

### Added
- Substitutes can be linked to user accounts (`substitutes.user_id`) and given an hourly wage (`substitutes.hourly_wage`). For a linked user, substitute shifts worked before the employment start date now appear in the personal views (day/week/month/year/statistics), marked as substitute shifts, and count towards hours, OB and pay, priced as hourly employment using the same calculations as existing hourly-paid users. Overtime is priced with the hourly wage in the personal view while `ot_pay` stays 0 in the database (the team view's source). The person-change flow has a new "Existing substitute" mode that creates the account, links the substitute and starts the employment in one transaction, and the substitute admin page can link retroactively and set the hourly wage. The monthly report hides a linked substitute's already-attributed days so nothing is counted twice

### Fixed
- The admin settings page now shows the error message when saving fails, for example on an invalid monthly salary. Previously the error was dropped silently and the page rendered an empty form
- Vacation payout for an employment change recorded on or after its own effective date could be calculated on the new direct salary instead of the consultant's actual final salary. The boundary day is now always priced with the salary that actually applied on that day
- OB, overtime and on-call rates on the exact day a rate change takes effect could be calculated with the new rates instead of the ones in force that day. The boundary day is now always priced with the rates that actually applied
- The day view now shows the same thing as the week, month and year views: accepted shift swaps are visible (previously not shown at all), parental leave and day-level vacation render as leave and vacation respectively, a full-day sick absence masks the shift code, and on-call is handled consistently across all views

### Deployment
- Run the migration `python migrations/migrate_substitute_account_link.py <db-path>` (adds `user_id` and `hourly_wage` to `substitutes`, idempotent). Back up the production database first: `sqlite3 app/database/schedule.db ".backup app/database/schedule.db.bak"`

## [0.17.0] - 2026-04-26

### Fixed
- API: `/next-shift` incorrectly returned today's shift; the endpoint now takes the time of day into account and skips shifts whose start time has already passed

### Added
- API: `/next-shift` now supports optional `?date` and `?time` parameters to simulate the response for an arbitrary point in time

### Planned
- Absence tracking (sick leave, VAB, other leave types)
- Wage history tracking for accurate historical calculations
- Rotation epochs support for changing rotation lengths
- Mobile UI improvements (FAB button, person dropdown)
- Complete iCal/ICS calendar export implementation

## Initial feature set - 2025-12-18

> Originally headed `[1.0.0]`. No 1.0.0 release has ever existed: the releases of
> this period were the `v0.0.x` series, the last being `v0.0.17` on 2025-12-10,
> and versioning resumed at `v0.12.0` in April 2026. This section records the
> feature set as it stood when the file was first written, not a tagged release.
> The heading is corrected so the number stays free for an actual 1.0.0.

### Added
- **User Authentication System**
  - JWT-based authentication with secure token handling
  - bcrypt password hashing for secure credential storage
  - Role-based access control (admin vs regular users)
  - Forced password change on first login
  - User profile management with password change functionality

- **10-Week Rotation Schedule System**
  - Automatic rotation through N1 (Day), N2 (Evening), N3 (Night) shifts
  - Configurable rotation start date and cycle length
  - Support for OFF days in rotation pattern
  - Week-based rotation tracking with ISO week numbers

- **OB (Inconvenient Hours) Pay Calculations**
  - OB1: Evening hours (18:00-22:00 weekdays)
  - OB2: Night hours (22:00-06:00)
  - OB3: Weekend hours (Saturday-Sunday)
  - OB4: Holiday hours (Swedish public holidays)
  - OB5: Major holiday hours (Christmas Eve, New Year's Eve, Midsummer Eve)
  - Automatic Swedish holiday calculation (Easter, Midsummer, Christmas, etc.)
  - Priority-based OB rule selection for overlapping periods

- **On-Call/Standby (Beredskap) Management**
  - OC shift type for on-call duty
  - Separate pay calculation for standby hours
  - Integration with rotation schedule

- **Overtime Tracking**
  - Database-persisted overtime shifts
  - Add/delete overtime functionality
  - Overtime pay calculation based on monthly salary
  - Display in day, week, month, and year views

- **Vacation Management**
  - Per-user vacation tracking by ISO week number
  - Multi-year vacation planning
  - Vacation display in calendar views
  - SEM (Semester) shift type for vacation periods

- **Calendar Views**
  - Dashboard with current and next week overview
  - Individual and all-team week views
  - Month calendar grid with ISO week numbers
  - Detailed day view with OB breakdown and pay calculations
  - Year summary with monthly breakdown
  - Co-working statistics showing shared shifts

- **Admin Panel**
  - User management (create, edit, delete users)
  - Password reset functionality
  - Settings management (rotation configuration, tax brackets)
  - Wage configuration per user
  - Admin-only access to all user salaries

- **ICS Calendar Export**
  - Export next 6 months of schedule
  - Language support (Swedish/English)
  - Compatible with Google Calendar, Outlook, Apple Calendar
  - Accessible from user profile page

- **Production Features**
  - Structured JSON logging with request IDs
  - Sentry error tracking integration (optional)
  - CORS configuration for production security
  - Health check endpoint for monitoring
  - Request logging middleware with performance tracking
  - Environment-based configuration via .env file

- **Development Infrastructure**
  - Docker support with Dockerfile and docker-compose
  - GitHub Actions CI/CD pipeline
  - Automated testing on pull requests
  - Automated deployment to production
  - Pre-commit hooks with ruff linting
  - Comprehensive test suite (pytest)
  - Database backup and restore scripts

- **Documentation**
  - Comprehensive README with quick start guide
  - ARCHITECTURE.md with detailed technical documentation
  - DEPLOYMENT.md with production deployment guide
  - CORS.md for CORS configuration
  - LOGGING.md for structured logging setup
  - SENTRY.md for error tracking configuration
  - FILE_PERMISSIONS.md for security setup

### Technical Details
- **Framework:** FastAPI (async Python web framework)
- **Database:** SQLAlchemy ORM with SQLite (PostgreSQL compatible)
- **Authentication:** JWT tokens with bcrypt password hashing
- **Templates:** Jinja2 with responsive CSS
- **Testing:** pytest with comprehensive test coverage
- **Linting:** ruff with pre-commit hooks
- **Python Version:** 3.11+
- **Deployment:** Docker, systemd, nginx/Traefik reverse proxy

### Security
- Secure password hashing with bcrypt
- JWT token-based authentication
- Role-based access control
- CORS protection in production
- File permission setup scripts
- Environment variable configuration
- Non-root Docker user

### Performance
- Request ID tracking for debugging
- Performance timing in logs
- Database query optimization
- Lazy loading for year totals
- Efficient OB calculation caching

## Database migration and refactoring - 2025-12-08

> Originally headed `[0.0.20] - 2024-12-XX`. No 0.0.20 release exists; the
> `v0.0.x` series ended at `v0.0.17`. The work described here shipped across
> `v0.0.9` to `v0.0.11`, all tagged on 2025-12-08.

### Changed
- Migrated from file-based storage to SQLite database
- Refactored schedule module into modular package structure
- Improved error handling across application

### Added
- Database migrations for users, overtime, and password changes
- Structured logging system
- Request logging middleware

## [0.0.1] - 2025-12-08

### Added
- Initial project setup
- Basic rotation schedule calculation
- File-based data storage (JSON)
- Simple web interface

---

## Version History Notes

- **Initial feature set** (untagged): First complete feature set, shipped as the `v0.0.x` series
- **Database migration and refactoring** (untagged): shipped across `v0.0.9`-`v0.0.11`
- **v0.0.1**: Initial prototype

For detailed commit history, see: `git log --oneline --decorate`
