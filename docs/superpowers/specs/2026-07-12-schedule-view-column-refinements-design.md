# Schedule view column refinements

Date: 2026-07-12
Branch: feat/person-centric-columns
Depends on: PR #274 (merged to main as of this branch's base)

## Background

PR #274 introduced position swaps (`swap_positions`) and made the team
week/month/year views (`show_week_all`, `show_month_all`, `show_year_all` in
`app/routes/schedule_all.py`) render one column per *holder segment* of a
rotation position, so a mid-period change shows the old and new holder
separately. This is correct for successions (a person leaves, someone else
takes the position) but produces confusing duplicate rows/columns for a pure
**swap** between two people who are both still active: each of the two people
gets two column entries (one per position they held during the period),
instead of one column that switches its data source at the swap date.

Three concrete problems were observed against real data on `main`:

1. `/year?year=2026`, "show past days" on: Rickard and Okan (who swapped
   positions 3 and 8 on 2026-10-01) each show two columns instead of one.
2. Same root cause reproduces in `/week` and `/month` (all-persons views),
   which build columns with the same per-position-segment logic.
3. `/week?year=2026&week=35` shows a "Vacant (#5)" row that renders nothing
   but OFF all week, for a position with a genuine employment gap.

A fourth, unrelated but adjacent issue was also found and is in scope here:
personal `/month/{id}` and `/week/{id}` remain viewable for any month/week
after a departed user's last working day (rendering an all-OFF page, per the
employment-end masking added in commit `313a7df`). That's technically correct
but not useful — the user wants those requests redirected to the team view
instead.

## Goals

1. A person who holds multiple rotation positions during a displayed period
   (via a swap, not a succession) gets **one** column/row in `/week`,
   `/month`, `/year`, not one per position segment.
2. A rotation position with **no holder at all** during the entire displayed
   period is not shown as a row/column, instead of a "Vacant" row/column that
   only ever renders OFF.
3. Requesting a departed user's personal `/month/{id}` or `/week/{id}` for a
   period entirely after their last working day redirects to the
   corresponding team view (`month_all` / `week_all`) instead of rendering an
   empty page.

## Non-goals

- No change to how genuine successions are displayed (departed user keeps
  their own column for their own tenure; successor gets their own column).
  Confirmed via the existing `test_year_by_user_id_shows_old_holder`-style
  regression coverage, which must keep passing unchanged.
- No change to salary/access-control logic (handled separately, see commit
  `b93ac0f` on this branch, already done).
- No change to the personal `/year/{id}` redirect-on-departure behavior
  (out of scope; only `/month/{id}` and `/week/{id}` are addressed here,
  matching what was actually requested).
- Cowork/handover view employment scoping remains tracked separately as
  issue #275; not addressed here.

## Design

### Correction after code review (2026-07-12, before implementation)

The three team views do **not** share one column-builder today. Each uses a
genuinely different mechanism, so this design fixes each one separately
instead of extracting a single shared helper:

- **Week** (`_build_person_rows` in `app/routes/schedule_all.py:55-144`):
  pre-resolves full **row dicts with a `cells` list** in Python. The outer
  loop is `for pid in range(1, 11)`; for each position it fetches
  `get_position_holder_segments` and, for 2+ segments, emits one row per
  segment with `cells` built by masking `_cell_for(day, pid)` to each
  segment's `from_date`/`to_date`. Each cell already carries everything the
  template needs (`shift`, hours, etc.) verbatim from `day.get("persons")`
  (which contains every position's data for that day already).
- **Month** (`show_month_all` in `app/routes/schedule_all.py:194-318`):
  pre-resolves full **column summary dicts** in Python. Same outer
  `for pid in range(1, 11)` loop; for 2+ segments it calls
  `summarize_month_for_person(..., year_days=masked_days, wage_user_id=seg["user_id"])`
  once per segment (each masked to that segment's own date range via
  `mask_days_to_employment`), producing one aggregate summary dict per
  segment (with its own `days` list, `total_ob`, `netto_pay`, etc.).
- **Year** (`show_year_all` in `app/routes/schedule_all.py:322-434`):
  builds lightweight **header metadata only** (`person_headers`, no shift
  data attached) and the template does the day-level lookup itself via
  `day.persons | selectattr('person_id', 'equalto', col.person_id) | first`
  (`year_all.html:101`) against a single shared `days_in_year` blob that
  already contains every position's data for every day.

Because week and month already fully pre-resolve their output in Python (no
template changes needed - the merged row/column just needs to look exactly
like a normal one), only **year** needs a template change. All three need
the same underlying idea: **group segments by `user_id` across positions,
not just within one position**, and only emit multiple rows/columns for a
user when their segments *don't* stitch into continuous coverage of a single
position.

### 1. Week view (`_build_person_rows`)

Restructure the function to build a `user_id -> [(pid, seg), ...]` map across
*all* positions first (instead of emitting rows inside the per-position
loop), then:

- A `user_id` with segments in exactly one position: emit exactly today's
  single row (no behavior change - this is every non-swap case, including
  ordinary successions, which keep their own separate rows since the
  departed and successor are different `user_id`s).
- A `user_id` with segments across 2+ positions (a genuine swap or move):
  emit **one** row. Build `cells` day-by-day: for each day in the displayed
  week, determine which of the user's segments covers that specific date
  (by date range) and pull `_cell_for(day, that_segment_pid)` - falling back
  to `_off_cell` for any day not covered by any of their segments (a real
  gap, not a swap).
- A position with zero segments and `has_position_history`: today emits a
  placeholder vacant row (`vacant: True`, all-OFF cells). Per Goal 2, emit
  **no row** for this case instead.

### 2. Month view (`show_month_all`)

Same regrouping by `user_id` across positions. The tricky part is combining
two (or more) `summarize_month_for_person` results into one, since each
segment's call only has that segment's own masked days and its own partial
totals. Add a small merge helper:

```python
def _merge_month_summaries(summaries: list[dict]) -> dict:
    """Combine same-month summaries for segments that don't overlap in time.

    Each input summary was built from year_days masked to one segment's own
    date range (all other days already zeroed/OFF via mask_days_to_employment).
    Since the segments are date-disjoint by construction (position history
    never has two open segments for the same position at once), merging is:
    take the first summary's `days` list and overlay any non-OFF day from
    later summaries, and sum every numeric aggregate field (they are 0/None
    for days outside that summary's own segment, so summing is safe).
    """
```

Only positions with 2+ segments belonging to 2+ *different* `user_id`s stay
as separate columns (unchanged - a real succession). Positions with 2+
segments belonging to the *same* `user_id` (a swap) call
`_merge_month_summaries` on their per-segment results and emit one column.
Zero-segment vacant positions emit no column (Goal 2), same as week.

### 3. Year view (`show_year_all` + `year_all.html`)

Group `person_headers` by `user_id` across positions the same way. For a
swap participant, emit one header with:
- `col_key = f"user-{user_id}"` (new format for merged headers only -
  existing single-position headers keep `f"{pid}-{user_id}"` unchanged, so
  existing tests and any already-saved `localStorage` state for non-swapped
  people are untouched).
- `person_id` = `get_user_person_id(db, user_id, on_date=real_today)` -
  "where they are right now" from the viewer's perspective, driving both
  the `#N` label and sort position (confirmed product decision: before the
  swap date the column reads at their start-of-period position, after it
  reads at their current one - this single `real_today`-relative resolution
  produces exactly that automatically as the calendar date advances).
- `position_by_date`: a `{date.isoformat(): person_id}` dict built from the
  merged segments, so the template can resolve which position's cell to pick
  per day instead of the fixed `col.person_id`.
- `past`/`future`: computed against the merged span's overall min
  `from_date` / max `to_date` (a swap participant is never past-or-future as
  a *whole* column - only a genuine departure is, and departures stay on
  separate `user_id`s so this doesn't interact with the swap case).

Zero-segment vacant positions emit no header (Goal 2), replacing today's
`vacant: True` placeholder header entirely.

Template change in `year_all.html`: the cell lookup at line 101
(`{% set person = (day.persons | selectattr('person_id', 'equalto', col.person_id) | list | first) %}`)
needs an `if col.position_by_date` branch that looks up
`col.position_by_date[day.date.isoformat()]` first and uses *that* as the
effective `person_id` for the `selectattr` call on merged columns; unmerged
columns (no `position_by_date` key) render exactly as today.

### 4. Redirect departed users away from post-tenure /month and /week

In `show_month_for_person` and `show_week_for_person`
(`app/routes/schedule_personal.py`), after resolving `target_user`,
`rotation_position`, and the viewer's own `emp_start`/`emp_end` (already
computed today for the OFF-masking added in `313a7df`): if the *entire*
requested month/week falls after `emp_end` (i.e. `emp_end is not None and
requested_period_start > emp_end`), redirect to `month_all`/`week_all` for
the same year/month or year/week instead of rendering the page. If the
period only partially overlaps their tenure (the normal case handled by
`313a7df`'s masking), render as today - no redirect, OFF only for the
specific out-of-tenure days.

This does not apply to `/year/{id}` (explicitly out of scope, confirmed) and
does not change the case where the *viewer themselves* is the one within
their own tenure looking at a valid month - only the case where every single
day of the requested period is after their own last day.

## Testing

- New tests in `tests/test_schedule_views_person_change.py` (or a new
  `tests/test_schedule_all_person_centric.py` if the existing file is
  getting large) for the Rickard/Okan swap scenario:
  - `/year?year=2026` with `simulated_date` after the swap: one column per
    person, correct `#N` label, correct shift on each side of the swap date.
  - Same for `/month?year=2026&month=10` (swap lands mid-month-view via
    padding, and within-month).
  - Same for `/week?year=2026&week=40` (swap lands mid-week).
  - Regression: Robin/Peter (genuine succession) still produces two separate
    columns/rows in all three views, unaffected.
- Vacant-row hiding: a position with a real employment gap and zero overlap
  with the displayed period produces no header at all; a position with a
  partial-period gap (holder leaves mid-period, gap, successor arrives before
  period end) still shows correctly (unaffected by this change - only
  *fully* vacant-for-the-whole-period positions are hidden).
- Redirect: `/month/10?year=2026&month=7` (Robin, entirely after his own
  March 31 end) redirects to `/month?year=2026&month=7`; `/month/10?year=
  2026&month=3` (his own last real month) still renders normally, no
  redirect. Same pair of cases for `/week`.

## Risks / open questions

- `col_key` format changes for swap-merged columns (`user-{id}` instead of
  `{pid}-{id}`). The year view's localStorage-persisted column visibility
  keys off `col_key` (`p_filter_cols_v2`, `filters.js`/inline script in
  `year_all.html`). A user who already hid/showed columns before this change
  ships will have stale keys for anyone who's ever been part of a swap -
  acceptable one-time reset, not worth migrating.
- `show_week_all` and `show_month_all` don't currently accept a
  `simulated_date` query param the way `show_year_all` does; tests for those
  two views can seed `PersonHistory` rows relative to fixed dates instead
  (as `test_schedule_views_person_change.py` already does elsewhere) rather
  than requiring the same testing hook - no product need to add it there
  identified during this spec.
- Month view merge correctness depends on segments truly being date-disjoint
  per position (guaranteed by `_create_employment_record`'s "one open record
  per position" invariant), so `_merge_month_summaries` can safely sum
  aggregate fields without double-counting - this is asserted, not just
  assumed, by the plan's tests.
