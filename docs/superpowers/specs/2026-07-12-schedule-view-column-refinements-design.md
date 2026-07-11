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

### 1. Shared column-builder helper

`show_week_all`, `show_month_all`, and `show_year_all` each currently contain
a near-identical block that loops `for pid in range(1, 11)`, calls
`get_position_holder_segments(db, pid, start, end)`, and appends one header
dict per (position, holder) pair. Extract this into a single function:

```python
def build_person_headers(
    db: Session,
    start_date: date,
    end_date: date,
    real_today: date,
) -> list[dict]:
    """One header per person who held any position during [start_date, end_date].

    A person who held a single position throughout keeps the exact shape
    already produced today (col_key, person_id, user_id, name, past, future).
    A person who held two or more different positions during the period
    (a swap) gets ONE header spanning all of their segments, with an added
    `position_by_date` map so callers can resolve which position's data
    applies to a given date within the merged span. A position with zero
    holder segments in the period (fully vacant throughout) yields no header
    at all - the caller's column/row list simply won't include it.
    """
```

Algorithm:

1. For each position 1-10, fetch `get_position_holder_segments` as today, and
   merge consecutive same-user segments within one position exactly as the
   current code already does (unchanged).
2. Group all resulting (position, user, segment) entries by `user_id` across
   *all* positions, not per-position.
3. For a `user_id` with segments in exactly one position: emit a header
   identical in shape to today's (`col_key = f"{pid}-{user_id}"`,
   `person_id = pid`, no `position_by_date`). This is the common case and
   must not regress any existing test.
4. For a `user_id` with segments across two or more positions (a swap, or
   more than one move) within the period: emit **one** header:
   - `col_key = f"user-{user_id}"` (new format for this case only -
     unambiguous, distinguishes merged headers from single-position ones for
     the template/JS filtering code).
   - `person_id` = the position resolved via
     `get_user_person_id(db, user_id, on_date=real_today)` - i.e. "where they
     are right now" from the viewer's perspective. This drives both the `#N`
     label and the column's sort position in the header row (per the
     product decision: before the swap date view shows them at their
     start-of-period position, after the swap date view shows them at their
     current one - this single `real_today`-relative resolution produces
     exactly that when the page itself is loaded before/after the swap
     date).
   - `position_by_date`: a `{date.isoformat(): person_id}` dict covering
     every day in `[start_date, end_date]`, built from the merged segments
     (each segment contributes its own position for its own date range).
   - `past` / `future`: computed the same way as today but against the
     merged span's overall `from_date`/`to_date` (a swap participant is
     never "past" or "future" as a whole - only a genuine departure is).
5. A position with `has_position_history` but zero segments overlapping the
   period keeps producing... nothing (see Goal 2 - this replaces today's
   "vacant" header entirely, it does not get a header). A position with *no*
   history at all keeps today's legacy fallback header unchanged (this case
   has nothing to do with position history and must not be touched).

All three routes (`show_week_all`, `show_month_all`, `show_year_all`) call
this one helper instead of their own duplicated loop.

### 2. Cell/day lookup for merged headers

Today's templates pick a day's data via
`day.persons | selectattr('person_id', 'equalto', col.person_id) | first`
(see `year_all.html:101`, and the equivalent in `week_all.html` /
`month_all.html`). This still works unchanged for every column that has no
`position_by_date` (the common case, per step 3 above).

For a column that does have `position_by_date`, the template needs the
*per-day* position, not the column's single `person_id`. Since Jinja cannot
cleanly do a dict lookup keyed by a loop variable's `.isoformat()` inline in
older syntax, add a small template filter/global (or pre-resolve server-side,
preferred): before rendering, for each such column, build a *resolved days*
list once per column (reusing the existing per-day `days_in_year`/
`days_in_month`/`days_in_week` fetch, just picking a different `person_id`'s
entry per date), and pass `col.resolved_days` alongside the normal per-date
lookup. The template checks `if col.resolved_days is defined` and indexes
into it by date instead of doing the `selectattr` against a fixed
`col.person_id`. This mirrors the "only touch the affected columns" approach
already used for the padding-day fix in commit `e7f75cb`; no change to the
underlying `generate_year_data`/`generate_period_data` fetch layer.

### 3. Redirect departed users away from post-tenure /month and /week

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
- The shared `build_person_headers` helper needs `real_today` threaded in
  from each route the same way `simulated_date` already is for
  `show_year_all`; confirm `show_week_all`/`show_month_all` already have
  (or gain) the same `simulated_date` testing hook for consistent test
  coverage across all three.
