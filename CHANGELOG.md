# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries here are written in English; this file documents the project for
developers. User-facing release notes are separate and bilingual: they live in
`data/releases.json`, which renders in the language the user
has selected. Add both when a change is worth telling users about.

A version is a release, not a pull request. While the topmost version here has
no git tag yet, add your entry to it instead of starting a new one; the version
number is bumped by the pull request that follows a release, not by every
change. `scripts/release.sh` refuses to tag when the tag, `pyproject.toml` and
`data/releases.json` disagree.

A version number exists so a user can match what they see to a release note, so
a change with no user-facing behaviour does not get one. Those land under
`## [Unreleased]` here with no bump and no `data/releases.json` entry, and ship either with
`./scripts/release.sh --notag` or alongside the next version that does have
something to tell users about, whose pull request renames the heading.

## [Unreleased]

### Security
- Password resets and the seeder generate a random password per run instead of using a committed constant. `DEFAULT_PASSWORD = "London1"` sat in `app/core/constants.py` and `"Banan1"` sat inline in `migrate_to_db.py` for the admin account, in a public repository, so anyone who read the source knew what every freshly reset account was sitting on. The username scheme (`user01`..`user10`) is in the same repo and the login rate limiter allows five attempts per username per 15 minutes, so a guess was free. `must_change_password` does not close this: whoever gets in first holds the current password and completes the forced change themselves. The generated password is shown once, in the admin success message and in the seeder's final output; the request logger records paths without query strings, so it does not reach `logs/app.log`. The seeder now gives the admin account its own password rather than the one every user account shares. `tests/test_admin_reset_password.py` pins that two resets never match, which is what a reintroduced constant would fail

### Added
- `scripts/check_default_passwords.py` reports which accounts are still on a password that used to be committed. `must_change_password` does not answer that on its own: five places set the flag and four of them use a password the admin typed in, so the script tests the stored hashes directly instead. Read-only (`mode=ro`), writes a report file, and exits 1 when anything is exposed so it can gate a rotation step. `--db` is required rather than defaulting: `/opt/Periodical/...` exists on more than one host, and on the decommissioned server it answered for a June copy of the database instead of the live one. The report echoes the hostname, the resolved path and the file's last write so a wrong-database run is visible rather than silent

### Fixed
- The deploy shipped the previous release and reported success when a release tag was repointed. `deploy.sh` fetched with `git fetch --tags`, which refuses to move a tag the host already has and says so as a warning rather than an error, so the `git checkout "$TAG"` that follows resolved the stale local tag. The service restarted, the health check passed and the deploy went green with prod still on the old commit. Fetching with `--force` is the whole fix; the warning is easy to miss in a log that is otherwise all pip output
- `release.sh` read `pyproject.toml` and `changelog.py` from the working tree and then checked out main and tagged that, so it validated one tree and tagged another. Run from an unmerged release branch the check passed against the branch's own bumped version and tagged main without the release on it, producing a deploy of the previous release under the new tag. It now reads both versions from `origin/main` and says to merge first when they do not match
- Every shell script under `scripts/` except `release.sh` has been mode `100644` since it was added, so a fresh clone leaves them non-executable and `./scripts/dev.sh` fails with "Permission denied". The exec bit is set in git now, not just on whichever working copy someone chmodded
- `__all__` in `app/core/schedule/__init__.py` listed `"rotationrotation_start_date"`, two names run together, so `from app.core.schedule import *` raised `AttributeError`. The names are `rotation` and `rotation_start_date`, and both are exported now

### Changed
- `data/persons.json` is gone, and with it the second roster the schedule code kept beside the `users` table. Six modules read names and wages out of it as a last-resort fallback via `persons[person_id - 1]`, index-into-a-file arithmetic that only lined up as long as the file listed every position in order. The file has held anonymised placeholders since PR #245, so every one of those lookups could only ever return `"Person N"` and a flat 30000. The name fallbacks return `placeholder_person_name(person_id)` now, the same string without the file, and `get_all_user_wages` without a session returns `settings.monthly_salary` per position, which is already what the session path gives a position whose user row is missing. No route reaches that branch: every one of them passes a session
- `migrations/migrate_to_db.py` builds its seed roster itself rather than reading `persons.json`, since what it read was `userNN` / `"Person N"` / one wage / no vacation, all derivable. It seeds the 10 rotation positions; the file listed an 11th placeholder that no rotation position corresponds to. It also deletes the database named by `DATABASE_URL` rather than a hardcoded `app/database/schedule.db`, so pointing it at another database no longer wipes the default one while writing to the other
- The user-facing release notes moved from a 1200-line `VERSIONS` literal inside `app/routes/changelog.py` to `data/releases.json`, loaded by `app/core/news.py` like every other file in `data/` and validated at startup with them. A route module was the place both `news.py` and `main.py` reached into for the application's version number, which is what forced the import in `news.py` to be function-local to break the resulting cycle; that hack is gone, and `main.py` imports the version with its other imports rather than after the lifespan handler. `scripts/release.sh` reads the version from the JSON now, the same `grep`/`cut` against a different path. `app/routes/changelog.py` is 24 lines and holds only its route
- Passwords are hashed by calling `bcrypt` directly instead of through passlib's `CryptContext`. The app has only ever used one scheme, and passlib has been unmaintained since 2020 to the point of logging a traceback on every import, reading a `bcrypt.__about__` attribute that no longer exists. Same `$2b$` format at the same cost factor 12, so stored hashes keep verifying and nobody has to reset a password; `tests/test_auth_primitives.py` pins a passlib-written hash as a literal to hold that. A hash bcrypt cannot parse is now a failed login rather than a 500, which is what a corrupt value in the column used to produce
- Access tokens are signed and validated with `hmac` from the standard library instead of python-jose, which pulled in ecdsa, rsa, pyasn1 and six to serve one symmetric algorithm with one key, and whose algorithm-confusion advisories were all in the asymmetric and JWE paths this app never used. The wire format is unchanged, so sessions issued before the swap keep working and the deploy logs nobody out; a jose-issued token is pinned as a literal in the tests. The HS256 header is a constant rather than something read from the token, which is what makes `"alg": "none"` and HS256/RS256 confusion impossible here rather than merely checked for, and `exp`, the signature and the `sub` type are all validated as jose validated them. `exp` is computed with `calendar.timegm` because `utcnow()` returns naive UTC and `datetime.timestamp()` would read it as local time, shifting every expiry by the Stockholm offset
- `period.py`, `summary.py`, `cowork.py` and `ob.py` each kept their own module-level lazy singleton over the same config loaders, five copies of `_x = None; if _x is None: _x = load_x()`. The loaders carry `@cache` now and the copies are gone. `load_tax_table` drops its hand-rolled per-year dict for `lru_cache`, bounded rather than unbounded because the year arrives from user data
- Schema migrations are one script instead of one file per change. `migrations/migrate_schema.py` reads the SQLAlchemy models, creates the tables the database is missing and adds the columns an existing table is missing, deriving the DDL (including the `NOT NULL DEFAULT` that SQLite requires to add a column to a table with rows in it) from the model itself. It is idempotent, takes `--dry-run`, and targets whatever `DATABASE_URL` points at, so it can run on every deploy rather than being remembered. Adding a column to a model no longer needs a migration file at all. A NOT NULL column whose model default cannot supply a value for existing rows stops the run instead of guessing one. Migrations that rewrite existing rows are still one-off scripts in their own file, since only their author knows what the rewrite means

### Removed
- `data/persons.json`, the `Person` pydantic model and `load_persons()`. See Changed for what replaced the fallbacks that read them
- `create_username()` in `migrate_to_db.py`, which no caller had used since usernames started coming from the roster rather than being derived from names
- The `passlib` and `python-jose` dependencies, and with them the `ecdsa`, `rsa` and `pyasn1` packages they pulled in. Five fewer packages in the install, and the two direct ones both sat in the authentication path. `six` stays, `python-dateutil` still wants it
- The 35 applied migration scripts, ~2400 lines that were the same sqlite3 connect / `PRAGMA table_info` / `ALTER TABLE ADD COLUMN` boilerplate with one column name changed. Every one of them had been run: the columns and tables they add are present in the current schema, and production is on 1.5.0, which is downstream of all of them. `migrate_schema.py` covers what they did going forward, and git history has them if an older database ever turns up. `migrate_to_db.py`, which seeds a fresh install, stays
- `tests/test_substitute_account_link_migration.py`, which tested one of the removed scripts. `tests/test_migrate_schema.py` covers the same ground for every column instead of one
- `app/core/types.py`: 116 lines of `NewType` aliases and `TypedDict`s (`PersonId`, `DayInfo`, `MonthSummary`, `NavigationDates` and the rest) that no module imported
- `app/core/config.py`: the OB divisor and time-format constants in it had no users at all, and its one live constant, `DATE_FORMAT_ISO`, was a `strptime` format for a date already in ISO form. The single call site uses `date.fromisoformat` instead
- Unused constants from `app/core/constants.py`: the per-code `SHIFT_CODE_*` aliases, `SHIFT_CODES`, `OB_CODES`, `OB_CODES_FOR_SUMMARY`, `DAYS_PER_WEEK`, `HOURS_PER_DAY`, `SECONDS_PER_HOUR` and `WEEK_START_WEEKDAY`. `WORK_SHIFT_CODES` and `VACATION_CODE` were the only readers of the aliases and now hold the literals
- `capture_exception()` and `capture_message()` from `sentry_config.py`, `LogContext` from `logging_config.py`, `log_security_event()` from `request_logging.py`, and the `OvertimeShiftCreate`/`OvertimeShiftResponse` models: no callers, and the Sentry pair only forwarded to the SDK
- Four templates nothing renders: `admin_rotation.html`, `admin_shift_types.html`, `index.html`, `test_today.html`
- The Docker, Traefik and nginx files under `deployment/`, plus its README. Production runs under systemd on bare metal, the root `Dockerfile`/`docker-compose.yml` build the dev container, and these described a Traefik deployment on `your-domain.com` that has never existed. `deployment/ica-schedule.service` stays, since that path is real. The nginx server block DEPLOYMENT.md pointed at is spelled out in DEPLOYMENT.md itself
- `benchmark.sh`: hand-rolled curl timing, referenced by no target, workflow or document

## [1.4.0] - 2026-08-05

### Added
- An "add a pay row" form on the payslip page. The select lists every pay type the app knows but did not produce for the month (`build_payslip_rows` skips rows that compute to zero, so a month with no OB3 previously had no row to edit), plus an "own label" option whose free-text label becomes the row key. `POST /month/<id>/payslip/override` no longer rejects a `row_key` outside `ROW_ORDER`, only a key that is empty or wider than the 40-character column; `apply_payslip_overrides` already created a row for a key the app did not compute. The sentinel for the free-text option is resolved server-side, so the form works with JavaScript disabled. No migration: rows are stored in the existing `payslip_overrides` table, per month like every other override
- The waiting-day deduction (karens) is its own payslip row, split out of the single net sick deduction using the `karens_hours` already carried on each absence detail. The two rows sum back to the figure the app computed, so gross pay does not move, and `COMPARE_BUCKETS` already grouped `karens` with the other sick rows so the upload comparison is unchanged
- The vacation supplement renders as two rows, fixed part and variable part, and a third for a variable part paid as a lump sum. Three new columns on `users` drive it, edited by the user themselves at `/profile/vacation` (and by an admin at `/admin/vacation/<id>`): `vacation_fixed_per_day` (a flat krona amount replacing the `fixed_pct` calculation), `vacation_variable_payout` (`per_day` or `lump`) and `vacation_variable_payout_month` (defaulting to the vacation year's start month). They deliberately do not live in `custom_rates`: that JSON is versioned through `RateHistory`, so a wage revision would open a new rate period and drag a payout routine along with it. Under `lump` the variable part leaves the per-day figure, or a taken vacation day would be paid its share twice; `full_supplement_per_day` keeps both parts for the payout path, which owes the whole supplement on every unused day regardless (Handelns avtal §9.5)
- The lump follows semesterlagen's percentage rule rather than the per-day supplement: `vacation_variable_lump_pct` (default 12%) of the variable pay that **fell due** during the earning year, not scaled by the entitlement. Because variable pay is normally paid the month after it is worked, the window is the earning year shifted back by `vacation_variable_lump_lag_months` (default 1), so March worked, April paid, is excluded from an earning year ending 31 March. The per-day 0.5% supplement deliberately keeps the unshifted window; both totals come out of one per-month pass, so the shift costs one extra month rather than a second loop. The supplement card names the window and the base it used (`12,0% av 22 967 kr utbetalt 2025-03 – 2026-02`) so the figure can be checked by hand

### Migration
- `migrations/migrate_vacation_payout_settings.py` adds the three `vacation_*` payout columns to `users`. Defaults reproduce today's behaviour exactly (no flat amount, variable part paid per vacation day). These settings briefly lived in `custom_rates["vacation"]`; any values found there are carried over to the columns and removed from the JSON, so an install that saw the intermediate state converges on the same result
- `migrations/migrate_payslip_karens_split.py` splits existing `sick_deduction` overrides now that karens is its own row. Before this release an override on that row meant "the whole sick block is this amount"; with the row split, an untouched override would sit on the smaller half with the karens row added on top, dropping gross by the karens amount. The migration rewrites each one into a `karens` override at the figure the app computes for that month plus a `sick_deduction` override at the remainder, so the pair sums to what the single override meant and gross does not move. Idempotent, supports `--dry-run`, and reports rather than guesses at months whose payslip cannot be rebuilt. Run it by hand on prod before deploying, like every other migration here

### Changed
- Manual payslip rows now reach the itemised figures on the month and year views, not only gross pay. `_OVERRIDE_TO_TOTAL` routes OB (into `ob_pay` under its own code), on-call, overtime and substitute pay alongside the absence and sick-pay rows it already handled. The per-day breakdown tables still show what the app computed, so both views mark an adjusted month from the new `override_deltas` in the summary rather than rewriting per-day data to match. `strip_salary_data` clears `override_deltas` with the other salary figures
- The month's vacation supplement is resolved in one place, `vacation_supplement_for_month`, instead of `supplement_per_day * days` repeated in the year view, the personal month view and the payslip route. The personal month view no longer skips the calculation when the month has no vacation days, since a lump payout lands in one month whether or not vacation was taken in it; a cheap check on the user's own settings (`is_variable_lump_month`) still gates the expensive balance calculation

### Fixed
- The all-persons week, month and range views showed the raw rotation shift for a departed holder's own last days, disagreeing with the day and personal week views about the same date. `_build_rotation_to_user_map` resolves a position through `User.person_id`, which only ever names the current or incoming holder, so the batch fetches never queried the departed user and their absences, shift overrides, overtime and on-call rows were dropped before any per-row date attribution ran. `_range_holder_user_ids` already recovered the real holders from `PersonHistory`, but was gated to the single-position views on the assumption that the all-persons views "already cover every active holder" - true for active holders, false for one whose segment has ended. It now runs for every view, and does so in one query instead of one per position, so covering all ten positions costs less than the old single-position path did
- The vacation supplement card printed the agreement's default rates ("0,8%", "0,5%") next to figures derived from the user's own configured rates. A user with a 0% variable rate saw "0,5%" above a 0 kr figure, which is exactly the case where the number needs explaining. Both notes now print the rate actually used, and a flat amount per day says so instead of showing a percentage
- `payslip_override_derived` claimed the row was "set by the vacation days", which is wrong for the variable lump: that one is a share of the earning year and does not move with the days taken. It now points at the vacation settings
- The variable-pay breakdown on the vacation cards showed the unshifted earning year even under lump payout, so it summed to a different total than the lump printed directly above it (36 510 vs 22 967 for the same person) with no period named on the personal page to explain the gap. `calculate_vacation_pay` now resolves which window the breakdown covers, so it always matches the payout the card is about, and both pages print that window
- The per-day supplement total printed a double colon ("Totalt 13 dagar:: 2 068 kr"): the translation already ends in one and the template added another

## [1.3.0] - 2026-07-28

### Added
- The manual overtime form in the personal day view has one quick-fill button per standard shift (N1, N2, N3). Clicking one sets start time, end time and hours; all three fields stay editable afterwards. The times come from `get_shift_types()` rather than the template, so they follow `data/shift_types.json`, and the hours are computed client-side with a midnight wrap so the night shift yields 8.5 rather than a negative number
- A per-month payslip page at `/month/<id>/payslip`, linked from the month view. It lists every compensation type as its own row (monthly wage or worked hours, OB per level, on-call per type, overtime, vacation supplement, sick pay and the absence deductions) with quantity, unit price and amount, and reconciles to the month's gross to the öre for both hourly and monthly users. The rows are built by `build_payslip_rows()` in `app/core/schedule/payslip.py` from the same summary totals the month view already uses, so the page cannot drift from the month figures
- Upload comparison on the payslip page: upload the employer's payslip PDF and the app diffs it against the computed figures, bucket by bucket. Comparing per bucket rather than per row is deliberate, an employer splits sick leave into three lines (sick pay, sick deduction, waiting-day deduction) where the app carries one net figure, so a row-by-row diff would flag a correct month as three mismatches. The PDF parser (`app/core/payslip_import.py`, using `pypdf`) is rate-aware: it categorises a row on its label first, then on the à-price against the user's own configured rates, so the same "Faktor 1,24" line maps to the right OB level for a user whose OB rate is 51,03 and a different one whose rate differs. A row it cannot categorise is surfaced as unknown, never guessed
- Manual per-row overrides on the payslip page. When the employer pays an amount the app's model does not reproduce, the row can be overridden, and the override flows through `summarize_month_for_person` so the month, year, dashboard and API figures all follow it rather than only the payslip page. Stored in the new `payslip_overrides` table (`migrations/migrate_payslip_overrides.py`)
- A per-day toggle on vacation (SEM) days in the personal day view that excludes a day from the vacation balance and supplement while leaving the shift on the schedule. Use it when the employer counts fewer vacation days than were scheduled, for example a weekend day the app counts but the employer does not: the excluded day earns no supplement and returns to the balance. It is stored as `counts_as_vacation_day` on the absence (`migrations/migrate_absence_counts_as_vacation.py`) and read through a single `vacation_counts` day flag that `summarize_month_for_person`, the year view and the supplement fold in `schedule_personal.py` all honour, so the count cannot diverge between them

### Changed
- The payslip-style breakdown that previously rendered inline in the month view for hourly users only (`hourly_breakdown` in `app/routes/schedule_personal.py`) moved to the dedicated payslip page and now renders for monthly users too. The inline table, its `_compute_sjuklon_base` helper and the now-unused `get_user_wage` import were removed from the month route

### Fixed
- The payslip rows for hourly users were missing `(OB hours + absence hours) x hourly rate`: normal hours priced only the non-OB hours, and the absence hours that `_hourly_corrected_gross` pays back into gross had no row at all. Both are now priced with the same worked-hours and rate the gross correction uses, so an hourly month reconciles to the öre
- CSRF validation rejected every multipart form (file uploads) with a 403: `_form_token` only read `application/x-www-form-urlencoded` bodies. It now parses multipart bodies with the stdlib MIME parser to find the token field, still failing closed on any parse error
- The extension form in the day view computed hours as end minus start with no midnight wrap, so staying past midnight after an evening shift (22:30, home 00:30) gave a negative difference that the guard discarded. The hours field stayed at 0 and `min="0.01"` then blocked submission with nothing on screen explaining why. A negative difference now wraps by 24h; an unchanged end time still yields 0 rather than a full day. Server side is unaffected: `POST /overtime/add` prices from the submitted hours, not from the times

## [1.2.0] - 2026-07-22

### Added
- The detailed OB / on-call / overtime breakdown now renders in the personal range view (`/range/<id>`), for any interval the view supports, with the same per-shift / per-calendar-day toggle as the month view. Export stays a month-view feature: neither the CSV nor the Excel button appears in the range view
- The breakdown has a second footer row with the amount per compensation column. It sums the pay already computed per day (`ob_pay` per code, the on-call breakdown's `pay`, and `ot_pay`) rather than multiplying total hours by a current rate, so a wage or rate change mid-period is priced per day. The OB columns carry the supplement only, matching wage codes 150-153 on the payslip; on-call and overtime carry the full amount. The normal-hours column is left blank: it has no separate compensation. The CSV export is unaffected, it reads `tbody` rows only

### Changed
- The breakdown table, its styles and its toggle scripts moved out of `month.html` into the shared partial `app/templates/breakdown_table.html`, included by both views. The rows are built by `build_range_breakdown_days()` in `app/core/schedule/summary.py`, which reuses `generate_period_data` and `_process_day_for_summary` and resolves OB rules per day's year, so a range crossing new year uses the right rules for each side of it

## [1.1.0] - 2026-07-21

### Added
- CSV export of the detailed breakdown in the personal month view, available to everyone who can see their own pay data (the Excel export for positions 6 and 8 is unchanged). The export is produced in the browser from the rows currently visible, so it always matches the per-shift / per-calendar-day mode on screen and cannot drift from the view. Column labels reuse the wage codes of the Excel export (`REPORT_COL_HEADERS` in `app/routes/excel_shared.py`), including its mapping of OB3 and OB4 onto code 152. Weekday, shift type and the total row are omitted as display aids; calendar-day mode additionally drops start, end and normal hours, which belong to the shift and are always blank there. Comma separated with decimal points (RFC 4180) and a UTF-8 BOM

## [1.0.0] - 2026-07-19

The first 1.0.0. The version number is earned by the security work and the pay
corrections below rather than by a headline feature: this is the release where
the app stops miscalculating people's money and stops being open to actions
performed in their name.

### Security
- CSRF protection on every state-changing route. Each form now submits a signed token that the server compares against a matching cookie, so another site cannot trick your logged-in browser into adding absence, changing wage data or performing other actions in your name. The API is unaffected because it authenticates with a key in the request header
- Logging out is now a button rather than a link, because a link could be triggered by another site to log you out without asking
- Development CORS no longer lets arbitrary sites make requests carrying your session cookie; it is restricted to loopback addresses

### Added
- The app now tells you when it has changed. While there are release notes you have not read, a "What's New" entry appears in the navigation with a small marker; opening it takes you to the changelog page and the entry disappears until the next release. It is not a permanent navigation item: it renders only while something is unread. What you have read is stored on your account (`users.seen_release`), so reading the notes on your phone also clears them on your desktop. Users who have never opened the page are treated as having unread notes, so everyone is pointed at the notes once after this ships
- Substitutes can be linked to user accounts (`substitutes.user_id`) and given an hourly wage (`substitutes.hourly_wage`). For a linked user, substitute shifts worked before the employment start date now appear in the personal views (day/week/month/year/statistics), marked as substitute shifts, and count towards hours, OB and pay, priced as hourly employment using the same calculations as existing hourly-paid users. Overtime is priced with the hourly wage in the personal view while `ot_pay` stays 0 in the database (the team view's source). The person-change flow has a new "Existing substitute" mode that creates the account, links the substitute and starts the employment in one transaction, and the substitute admin page can link retroactively and set the hourly wage. The monthly report hides a linked substitute's already-attributed days so nothing is counted twice

### Fixed
- Overtime booked on a vacation week replaced the vacation day (SEM) in the schedule views and was also counted as overtime pay. Vacation now takes priority over overtime, as absence and parental leave already did. The day renders as vacation again and no overtime pay is added. Day-level vacation entered as an absence was not affected
- The admin settings page now shows the error message when saving fails, for example on an invalid monthly salary. Previously the error was dropped silently and the page rendered an empty form
- Vacation payout for an employment change recorded on or after its own effective date could be calculated on the new direct salary instead of the consultant's actual final salary. The boundary day is now always priced with the salary that actually applied on that day
- OB, overtime and on-call rates on the exact day a rate change takes effect could be calculated with the new rates instead of the ones in force that day. The boundary day is now always priced with the rates that actually applied
- The day view now shows the same thing as the week, month and year views: accepted shift swaps are visible (previously not shown at all), parental leave and day-level vacation render as leave and vacation respectively, a full-day sick absence masks the shift code, and on-call is handled consistently across all views

### Deployment
- Run the migration `python migrations/migrate_user_seen_release.py <db-path>` (adds `seen_release` to `users`, idempotent). Existing rows stay NULL on purpose, which is what shows every user the release notes once
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
