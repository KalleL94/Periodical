# Day time segments, richer overtime, and multi-day editing

Date: 2026-09-12
Branches: `refactor/day-segments`, `feat/day-segment-types`, `feat/multiday-edit`

## Goal

Three things the day page cannot express today:

1. Overtime **before** a shift, not only after it.
2. Extra time before or after a shift that is **not** overtime.
3. A custom shift with a free-text label ("Övrigt") and its own clock times.

And two structural problems that block them:

4. The day page has grown to 1062 lines with four stacked forms in one edit
   panel. Three more forms make it unusable.
5. Every edit route takes exactly one date, so changing a week means seven
   visits to seven day pages.

## Decisions

**A day's worked time becomes a list of intervals.** Today
`_resolve_effective_shift` returns four scalars (shift, `hours`, `start`, `end`)
and OB is computed from that single interval in `_with_ob`. Each overlay rebinds
the same four scalars in turn. Non-overtime extra time cannot be expressed in
that shape without a second OB loop bolted next to the first, which is the same
amount of code as doing it properly. The day instead carries
`segments: list[DaySegment]`, OB is computed once by running
`calculate_ob_hours` over every OB-bearing segment, `hours` is the sum and
`start`/`end` are the min and max.

**The day dict keys do not change.** `shift`, `hours`, `start`, `end`, `ob`,
`ot_pay`, `ot_hours`, `ot_details`, `ob_hours_override`. summary.py, payslip.py,
excel_shared.py, statistics.py and api_v1.py are untouched by the refactor. The
1538 lines of characterization tests (`test_period_characterization.py`,
`test_summary_characterization.py`, `test_day_builder_agreement.py`,
`test_api_v1_characterization.py`) are the grader: they pass unmodified or the
refactor is wrong. All 72 were green at the time of writing.

**`shift_types.json` stays a JSON file.** A DB table is tempting because the
custom shift needs free clock times, but the custom shift stores its own times on
`shift_overrides`, so the closed JSON set still holds. Moving it to the database
buys a migration plus an admin CRUD page and zero new capability.

**The priority chain stays explicit.** Seven sources resolve a day's shift and it
is tempting to turn them into a list of `Layer` objects with priorities. They do
not behave alike: absence short-circuits and returns early, a vacation week
blocks the overtime overlay but not on-call, on-call is recomputed when overtime
exists on the day or crosses midnight from the day before, a shift override
replaces. Those are four behaviours, not four instances of one rule. A generic
layer system would hide them behind a shared interface instead of expressing
them, and the next bug in the chain would be harder to find, not easier.

**`overtime_shifts` is extended in place, not replaced.** The table already has
the segment shape: `user_id`, `substitute_id`, `date`, `start_time`, `end_time`,
`hours`, `ot_pay`, `created_by`. Two columns make it carry every segment kind.
A new `day_segments` table would mean a production data migration, two read paths
during the transition, and every consumer touched in one sweep. The table name
then understates what it holds; that is cheaper than a SQLite table rebuild and
16 reference updates.

**A multi-date post skips conflicting days and reports them; a single-date post
keeps today's upsert.** Silently overwriting ten days is the kind of error that
first shows up in a payslip forecast a month later, and rejecting the whole post
is worse still, since one conflicting day would block nine correct ones. One
date is different: `/absence/add` already replaces an existing absence on that
date, and the day page depends on it, because swapping VAB for SICK is exactly
that gesture. With one date you can see what you are replacing; with ten you
cannot. The rule is therefore on the count of dates posted, not on the route.

## Data model

### `overtime_shifts`: two new columns, one dropped

| Column | Type | Notes |
|---|---|---|
| `kind` | String(8) | `ot` or `extra` |
| `side` | String(6) | `before`, `after`, or `full` |

`is_extension` (Boolean) is dropped. SQLite 3.53.1 is installed, well past the
3.35 needed for `ALTER TABLE ... DROP COLUMN`.

`side = 'full'` is a sentinel, not NULL: SQLite treats NULLs in a unique index as
distinct, so a NULL side would leave called-in overtime unconstrained.

Two unique indexes, one per owner column:

```sql
CREATE UNIQUE INDEX ix_ot_user_day_kind_side  ON overtime_shifts (user_id, date, kind, side);
CREATE UNIQUE INDEX ix_ot_sub_day_kind_side   ON overtime_shifts (substitute_id, date, kind, side);
```

Each is a no-op for rows where its owner column is NULL, which is the wanted
behaviour since exactly one of the two is ever set. This replaces the manual
duplicate deletion in `/overtime/add`, which becomes an upsert per
`(owner, date, kind, side)`.

Backfill:

```sql
UPDATE overtime_shifts SET kind = 'ot', side = CASE WHEN is_extension THEN 'after' ELSE 'full' END;
```

### `shift_overrides`: three new nullable columns

| Column | Type | Notes |
|---|---|---|
| `start_time` | Time | nullable, set only for `shift_code = 'ETC'` |
| `end_time` | Time | nullable, same |
| `label` | String(40) | nullable, free text shown instead of a shift label |

`_ALLOWED_CODES` in `app/routes/shift_override.py` gains `ETC`. When the code is
`ETC`, `_resolve_effective_shift` builds a synthetic shift type from the three
columns, so OB falls out of `_with_ob` with no new pay code. One row per day
already, which matches "replaces the day's shift".

### `is_extension` stays in the output contract

The column goes, the field does not. `api_v1.py:183` returns `is_extension` in a
JSON payload and `ot_details["is_extension"]` is read by `excel_shared.py:125`
and `breakdown_table.html:138`. Both become derived: `side != 'full'`. The two
query filters that select called-in overtime (`api_v1.py:57`, `api_v1.py:152`)
become `side == 'full'`. `test_api_v1_characterization.py` pins the response
shape and enforces this.

## Pay semantics

| kind | side | Meaning | `ot_pay` | OB |
|---|---|---|---|---|
| `ot` | `after` | Overtime extending the shift (today's `is_extension`) | OT rate × hours | no |
| `ot` | `before` | Overtime before the shift, new | OT rate × hours | no |
| `ot` | `full` | Called-in overtime, replaces the day's shift | OT rate × hours | no |
| `extra` | `before` / `after` | Extra time, new | 0.0 | yes, on the segment's interval |

`extra` segments follow `User.wage_type`: `MONTHLY` gets the OB supplement only,
because the time itself is already inside the monthly salary; `HOURLY` gets base
hourly pay plus OB. This is the same branch that
`get_ot_hourly_rate_from_stored_wage` already leans on.

Overtime segments carry no OB, which preserves today's behaviour for extensions.

**Only `extra` segments join the segment list.** This is the rule the whole pay
story hangs on, and getting it wrong double-counts hours. `day_worked_hours` in
`summary.py` computes a day as `day["hours"] + day["ot_hours"]`, so anything that
lands in both is counted twice. Today an extension's hours live in `ot_hours`
alone and never touch `day["hours"]`, which is why an 8.5 h shift plus 2 h
overtime reports 10.5 and not 12.5.

So:

| kind | side | Joins the segment list? | Reported through |
|---|---|---|---|
| `extra` | `before` / `after` | yes | `day["hours"]` and `day["ob"]` |
| `ot` | `before` / `after` | no | `ot_hours`, `ot_pay`, `ot_details`, as today |
| `ot` | `full` | replaces it | `ot_hours`; `day_worked_hours` zeroes shift hours for code `OT` |

**The pay rule then needs no new code.** For `HOURLY` users `summary.py` already
prices `total_hours - ot_hours - substitute_hours` at the hourly rate, so extra
time entering `day["hours"]` is paid automatically. For `MONTHLY` users gross is
the fixed salary and is unaffected, while OB grows because `segment_ob` covers
the new interval. The `wage_type` branch described above is therefore a
description of what already happens, not a thing to build. The only new
computation is the OB over the extra segment.

Unchanged precedence: a vacation day still blocks the overtime overlay
(issue #285), absence and parental leave still short-circuit before any segment
is read, and `day_pay_override.ob_hours_override` is still applied last so a
manual hour correction wins over everything computed.

Overtime before a shift does **not** move the shift's displayed start time. It
renders as its own segment row, the way an extension does today.

## Branch 0: `refactor/day-segments`

Pure refactor. Identical output, no new fields, no new behaviour, no migration.

`_ShiftResolution` gains a `segments` field. `_with_ob` builds a one-element
list. `_apply_oncall_override` and `_apply_ot_display_shift` are rewritten to
operate on the list instead of rebinding scalars. `hours`, `start`, `end` and
`ob` are derived from the list at the end of `_populate_single_person_day` and
`_build_person_day_basic`.

`_apply_ot_display_shift` survives this branch, contrary to the first draft of
this spec. It exists only to fake the shift type to `OT` so a called-in overtime
day has something to display, and a segment list is what eventually makes that
fake unnecessary. But the day dict still reports a single `shift` and the
characterization tests pin it, so letting the templates read the segment list
instead changes observable output. That is branch 1 at the earliest. What this
branch does take from the function is the scalar rebinding: it returns
`(shift, segments)` instead of four values.

The same constraint applies to OB. The scalar version does not recompute OB after
the overtime overlay, so a called-in overtime day keeps the OB of the shift it
replaced. That is arguably wrong, and it stays wrong here.

Done when all four characterization test files pass **unmodified**. Any edit to
those files during this branch means the refactor changed behaviour.

## Branch 1 splits in two

The engine and the surface are independently testable, so they get one plan
each. 1a can be exercised entirely through unit tests and the routes; 1b is what
makes it reachable from a browser.

## Branch 1a: `feat/day-segment-types`

The migration above, then the three new capabilities.

`app/routes/overtime.py` gains `kind` and `side` form fields and upserts per
`(owner, date, kind, side)`. `/shift-override/add` accepts `ETC` with times and
a label.

## Branch 1b: `feat/day-edit-panel`

The edit panel becomes a Jinja partial, `app/templates/_day_edit_panel.html`,
with four tabs: Tid, Frånvaro, Beredskap, Byte. The Tid tab lists the day's
active segments with delete buttons, then one form whose type and side selects
decide what is being added. Tab switching is a CSS class toggle on a container,
matching the CSP-safe pattern already used for `#pay-section.is-editing`, so no
inline styles and no new dependency.

The partial takes a **list** of dates, not a date. The day page passes one. That
single decision is what makes branch 2 cheap; building the panel around a scalar
date means rewriting it in branch 2.

All four POST routes change `date: date_cls = Form(...)` to
`dates: list[date_cls] = Form(...)`: `/overtime/add`, `/absence/add`,
`/oncall/add`, `/shift-override/add`. The day page posts one value, the
multi-day drawer posts N, same field name and same route. FastAPI validates each
date. No branch in the handler, no second set of routes.

Each route also gains `return_to: str = Form("")`. With N dates there is no
obvious day to redirect to, and without this every multi-day edit would land on
an arbitrary day. The day page sends its own URL; the value is validated as a
relative path before use so it cannot become an open redirect.

### Conflict rules

These live in the routes, so they land in this branch even though nothing can
trigger them until branch 2 ships a way to select more than one date.

When one date is posted, each route behaves exactly as it does today: an upsert
that replaces what is there. When two or more are posted, a conflicting date is
left untouched and named in the result. The route redirects to `return_to` with a
`?result=` fragment rendering as, for example, "5 dagar satta. 1 hoppades över:
14 sep hade redan VAB."

| Route | A date conflicts when it has |
|---|---|
| `/absence/add` | an existing absence |
| `/shift-override/add` | an absence, or week-based vacation (not an existing override, which is replaced as today) |
| `/overtime/add` | the same `(kind, side)`, or a vacation day, since `is_vacation_day` already blocks the overtime overlay |
| `/oncall/add` | an existing on-call override |

`clear_schedule_cache()` is called once after the whole loop, not per date.

## Branch 2: `feat/multiday-edit`

Selection in the month and week views. No new pay code, no new routes.

`td.calendar-day` gains `data-date` and `aria-pressed`. One script in
`app/static/` handles click to toggle, shift-click for a range, and Escape to
clear. `month.html` and `week.html` already use identical markup
(`table.calendar-grid > td.calendar-day`), so one script drives both. The day
number keeps its link to the day page; the rest of the cell is the hit area.

`_day_edit_panel.html` is rendered once at the bottom of each view, hidden until
something is selected. It shows the selection ("4 dagar markerade: 14, 15, 16,
18 sep") and the same four tabs. The script writes the selected dates into hidden
`dates` inputs on submit, so opening the drawer needs no server round trip.

All four actions work across days: absence, shift change and custom shift,
overtime and extra time, on-call.

### Known ceiling

The loop writes one row per date with no batching. Selecting a full year means
365 inserts in one transaction. That is fast enough in SQLite, so the guard is a
cap on dates per post rather than batching logic. Marked in the code with a
`ponytail:` comment naming the cap and the upgrade path.

## Branch 3: `feat/per-day-values`

Branch 2's drawer applies one value to every selected day. This adds the case it
cannot express: different values per day, for example sick on Monday, child care
on Wednesday, overtime on Friday.

A fifth tab, "Per dag", renders one row per selected day. Above the table an area
select chooses Frånvaro, Tid or Pass, and only that area's columns are visible.
That is the mobile constraint driving the design: all three areas at once is
twelve controls per row, which cannot be a usable table at 400 px. One area at a
time keeps a row to three to five fields, so it degrades to a readable card.

**The rows are built in the browser.** The drawer renders before anything is
selected, so the server does not know which days the table needs. A hidden
`<template>` row is cloned per selected date, which is also why the table does
not prefill with each day's current values: that would need a round trip. Removal
is already covered by `/day-edit/clear`, so prefill buys less than it costs.

Fields are named `<area>_<field>_<ISO date>`, so the route groups by suffix rather
than trusting parallel arrays to stay aligned. The post also carries `area`, and
the route reads only that area's fields: switching area leaves the other two sets
in the DOM, and acting on them would write values the user never looked at.

New route `/day-edit/bulk`. It reuses `MAX_EDIT_DATES` and reports through the
same `?success=` fragment.

## Testing

Branch 0: the four characterization files, unmodified, are the entire
acceptance criterion.

Branch 1:
- A migration test in the shape of `test_migrate_schema.py`: backfill maps
  `is_extension` correctly both ways, and both unique indexes reject a duplicate
  `(owner, date, kind, side)`.
- OB on an `extra` segment, computed per `wage_type`: `MONTHLY` gets OB and no
  base pay, `HOURLY` gets both. Extends `test_ob_calculation.py`.
- Overtime on both sides of one day, summing into `ot_hours` and `ot_pay`, with
  `ot_details` reporting the `after` segment, since that is the only segment that
  can cross midnight and `summary.py:871` depends on it.
- A custom `ETC` shift override produces OB from its own times.
- `test_overtime_upsert.py` extended: the upsert is now per `(kind, side)`.
- A vacation day still suppresses overtime pay (issue #285 regression).
- A single-date post still upserts: posting SICK over an existing VAB day
  replaces it, rather than being reported as a conflict.

Branch 2:
- Posting four dates creates four rows and redirects to `return_to`.
- A conflicting date is skipped, the others are written, and the result names the
  skipped date.
- `return_to` rejects an absolute URL.

## Out of scope

- Substitute segments. The columns work for them, the forms come later.
- Selection in `month_all.html` and `week_all.html`.
- Splitting a shift into two separate worked blocks. The segment list makes it
  possible; nobody asked for it.
- Overtime rate tiers. A single OT rate per user stays, as today.
