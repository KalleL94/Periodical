# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- CSRF protection on every state-changing route. Each form now submits a signed token that the server compares against a matching cookie, so another site cannot trick your logged-in browser into adding absence, changing wage data or performing other actions in your name. The API is unaffected because it authenticates with a key in the request header
- Logging out is now a button rather than a link, because a link could be triggered by another site to log you out without asking
- Development CORS no longer lets arbitrary sites make requests carrying your session cookie; it is restricted to loopback addresses

### Added
- Vikarier kan kopplas till användarkonton (`substitutes.user_id`) och få en timlön (`substitutes.hourly_wage`). För en kopplad användare visas vikariepassen före anställningsstarten i de personliga vyerna (dag/vecka/månad/år/statistik), märkta som vikariepass, och räknas in i timmar, OB och lön – prissatta som timavlönade med samma beräkningar som befintliga timavlönade användare. Övertid prissätts med timlönen i personvyn medan `ot_pay` förblir 0 i databasen (lagvyns källa). Personbytesflödet har ett nytt läge "Befintlig vikarie" som skapar kontot, kopplar vikarien och startar anställningen i en transaktion, och vikarieadminsidan kan koppla retroaktivt och sätta timlön. Månadsrapporten döljer en kopplad vikaries redan attribuerade dagar så inget dubbelräknas

### Fixed
- Overtime booked on a vacation week replaced the vacation day (SEM) in the schedule views and was also counted as overtime pay. Vacation now takes priority over overtime, as absence and parental leave already did. The day renders as vacation again and no overtime pay is added. Day-level vacation entered as an absence was not affected
- Administratörens inställningssida visar nu felmeddelandet när sparandet misslyckas, till exempel vid en ogiltig månadslön. Tidigare försvann felet tyst och sidan visade bara ett tomt formulär
- Semesterutbetalning vid ett anställningsbyte som registrerades på eller efter sitt eget ikraftträdandedatum kunde räknas på den nya direktlönen i stället för konsultens faktiska slutlön. Gränsdagen prissätts nu alltid med den lön som faktiskt gällde den dagen
- OB-, övertids- och beredskapsrater på exakt den dag en ratändring träder i kraft kunde räknas med de nya raterna i stället för de som gällde den dagen. Gränsdagen prissätts nu alltid med de rater som faktiskt gällde
- Dagvyn visar nu samma sak som vecko-, månads- och årsvyn: accepterade skiftbyten syns (visades tidigare inte alls), föräldraledighet och dagsemester visas som ledighet respektive semester, en heldags sjukfrånvaro maskar passkoden, och beredskap hanteras konsekvent i alla vyer

### Deployment
- Kör migrationen `python migrations/migrate_substitute_account_link.py <db-path>` (lägger till `user_id` och `hourly_wage` på `substitutes`, idempotent). Ta backup av produktionsdatabasen först: `sqlite3 app/database/schedule.db ".backup app/database/schedule.db.bak"`

## [0.17.0] - 2026-04-26

### Fixed
- API: `/next-shift` returnerade felaktigt dagens pass – endpointen tar nu hänsyn till klockslag och hoppar över pass vars starttid redan passerat

### Added
- API: `/next-shift` stödjer nu valfria parametrar `?date` och `?time` för att simulera svaret för en godtycklig tidpunkt

### Planned
- Absence tracking (sick leave, VAB, other leave types)
- Wage history tracking for accurate historical calculations
- Rotation epochs support for changing rotation lengths
- Mobile UI improvements (FAB button, person dropdown)
- Complete iCal/ICS calendar export implementation

## [1.0.0] - 2025-12-18

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

## [0.0.20] - 2024-12-XX

### Changed
- Migrated from file-based storage to SQLite database
- Refactored schedule module into modular package structure
- Improved error handling across application

### Added
- Database migrations for users, overtime, and password changes
- Structured logging system
- Request logging middleware

## [0.0.1] - 2024-01-XX

### Added
- Initial project setup
- Basic rotation schedule calculation
- File-based data storage (JSON)
- Simple web interface

---

## Version History Notes

- **v1.0.0**: First production-ready release with complete feature set
- **v0.0.20**: Database migration and major refactoring
- **v0.0.1**: Initial prototype

For detailed commit history, see: `git log --oneline --decorate`
