# Schedule View Column Refinements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the team week/month/year views show one row/column per person (not one per position segment) when two currently-active people swap rotation positions, hide fully-vacant positions instead of showing an all-OFF placeholder row/column, and redirect a departed user's personal `/month/{id}` and `/week/{id}` views to the team view once the entire requested period is after their own last working day.

**Architecture:** Three independent, targeted fixes matching each view's existing (and different) mechanism - `_build_person_rows` for week, an inline per-position loop with a new `_merge_month_summaries` helper for month, and `person_headers` + a template branch for year - plus two small route-level redirect additions in `schedule_personal.py`. See `docs/superpowers/specs/2026-07-12-schedule-view-column-refinements-design.md` for full rationale.

**Tech Stack:** FastAPI, SQLAlchemy, Jinja2, pytest. Test DB via the existing `month_env` fixture in `tests/test_schedule_views_person_change.py`.

## Global Constraints

- No AI/Claude attribution in any commit message; commit messages in English, no em-dash.
- Run `venv/bin/python3 -m pytest` after every task; the full suite must stay green (currently 417 tests as of this branch's base commit `abecd14`).
- Do not touch `summary.py`'s absence-lookup fix, `period.py`'s batch-fetch date-resolution fix (`c1e30c6`), the successor-leak masking (`313a7df`), the `get_user_person_id` fallback fix (`d884f9a`), or the calendar padding-day fix (`e7f75cb`) - all already correct, already tested, out of scope here.
- Genuine successions (different `user_id`s holding the same position over time, e.g. Robin -> Peter) must keep rendering as separate rows/columns in every task below - re-run the existing tests named `test_year_by_user_id_shows_old_holder`, `test_mid_month_change_shows_both_persons`, and any week-view equivalent after each task to confirm.
- Commit after each task passes its own tests. Do not batch multiple tasks into one commit.

---

### Task 1: Week view - merge swap participants into one row, hide fully-vacant rows

**Files:**
- Modify: `app/routes/schedule_all.py:55-144` (`_build_person_rows`)
- Test: `tests/test_schedule_views_person_change.py`

**Interfaces:**
- Consumes: `get_position_holder_segments(db, pid, start, end) -> list[dict]` (each dict has `user_id`, `name`, `from_date`, `to_date`), `has_position_history(db, pid) -> bool` (both already exist, unchanged), `get_user_person_id(db, user_id, on_date=...) -> int | None` (already exists, unchanged).
- Produces: `_build_person_rows(db, days_in_week, monday, sunday) -> list[dict]` - same signature as today; each row dict still has `person_id`, `person_name`, `vacant`, `holder_user_id`, `cells` (list aligned with `days_in_week`). Callers (`show_week_all`) are unchanged.

- [ ] **Step 1: Write the failing test for the swap case**

Add to `tests/test_schedule_views_person_change.py` (near `test_ot_shift_stays_on_pre_swap_holder`, reusing its exact seeding pattern):

```python
def test_week_view_merges_swap_into_one_row(month_env):
    """A position swap between two active people yields ONE row per person,
    not one row per position segment.

    Rickard (user 11, position 3) and Okan (user 8, position 8) swap on
    2026-10-01. Week 40 2026 (2026-09-28 to 2026-10-04) straddles the swap.
    Each person must appear exactly once, with their own real shifts on each
    side of the swap date - not twice (once per position).
    """
    client, session = month_env
    admin = _make_user(session, 2, "admin1", "Admin", role=UserRole.ADMIN)
    rickard = _make_user(session, 11, "rickard1", "Rickard")
    okan = _make_user(session, 8, "okan1", "Okan")
    start_employment(session, rickard.id, 3, "Rickard", "rickard1", datetime.date(2026, 1, 2), created_by=admin.id)
    start_employment(session, okan.id, 8, "Okan", "okan1", datetime.date(2026, 1, 2), created_by=admin.id)
    swap_positions(session, 3, 8, datetime.date(2026, 10, 1), created_by=admin.id)

    token = create_access_token(data={"sub": str(admin.id)})
    client.cookies.set("access_token", f"Bearer {token}")
    resp = client.get("/week?year=2026&week=40")

    assert resp.status_code == 200
    assert resp.text.count(">Rickard<") == 1
    assert resp.text.count(">Okan<") == 1


def test_week_view_hides_fully_vacant_position(month_env):
    """A position with no holder at all during the displayed week shows no row."""
    client, session = month_env
    admin = _make_user(session, 2, "admin1", "Admin", role=UserRole.ADMIN)
    isak = _make_user(session, 5, "isak1", "Isak")
    start_employment(session, isak.id, 5, "Isak", "isak1", datetime.date(2026, 1, 2), created_by=admin.id)
    end_employment(session, isak.id, datetime.date(2026, 8, 3), created_by=admin.id)
    # Position 5 has a real gap: Isak left 2026-08-03, nobody holds it until
    # a successor (not seeded here) starts 2026-09-01. Week 35 falls in the gap.

    token = create_access_token(data={"sub": str(admin.id)})
    client.cookies.set("access_token", f"Bearer {token}")
    resp = client.get("/week?year=2026&week=35")

    assert resp.status_code == 200
    assert "Vacant" not in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python3 -m pytest tests/test_schedule_views_person_change.py -k "test_week_view_merges_swap_into_one_row or test_week_view_hides_fully_vacant_position" -v`
Expected: `test_week_view_merges_swap_into_one_row` FAILS (Rickard and Okan each appear twice - `count(">Rickard<") == 2`, not 1). `test_week_view_hides_fully_vacant_position` FAILS ("Vacant" is present in the response).

- [ ] **Step 3: Rewrite `_build_person_rows`**

Replace the full function body in `app/routes/schedule_all.py` (lines 55-144) with:

```python
def _build_person_rows(db: Session, days_in_week: list[dict], monday: date, sunday: date) -> list[dict]:
    """Build one week row per person holding a position during the week.

    A person holding a single position throughout the week (the common case,
    including an ordinary succession where a different person took over
    mid-week) yields exactly one row, masked to their own tenure as before.
    A person holding two or more DIFFERENT positions during the week (a
    position swap) is merged into ONE row: each day's cell is pulled from
    whichever position they actually held on that specific date. A position
    with no holder at all during the week is skipped entirely (no vacant
    placeholder row). Substitute entries (person_id outside 1-10) are
    appended unchanged.
    """
    from app.core.utils import get_today

    def _cell_for(day: dict, pid: int) -> dict | None:
        return next((p for p in day.get("persons", []) if p.get("person_id") == pid), None)

    legacy_rows: list[dict] = []
    segments_by_user: dict[int, list[dict]] = {}
    for pid in range(1, 11):
        segments = get_position_holder_segments(db, pid, monday, sunday)
        if not segments:
            if has_position_history(db, pid):
                continue  # Fully vacant for the whole week: no row.
            base_cells = [_cell_for(day, pid) for day in days_in_week]
            name = base_cells[0]["person_name"] if base_cells[0] else f"Person {pid}"
            legacy_rows.append(
                {
                    "person_id": pid,
                    "person_name": name,
                    "vacant": False,
                    "holder_user_id": pid,
                    "cells": base_cells,
                }
            )
            continue
        for seg in segments:
            segments_by_user.setdefault(seg["user_id"], []).append({**seg, "person_id": pid})

    real_today = get_today()
    merged_rows: list[dict] = []
    for user_id, segs in segments_by_user.items():
        segs.sort(key=lambda s: s["from_date"])
        positions_held = {s["person_id"] for s in segs}
        name = segs[-1]["name"]

        if len(positions_held) == 1:
            pid = segs[0]["person_id"]
            base_cells = [_cell_for(day, pid) for day in days_in_week]
            cells = []
            for day, cell in zip(days_in_week, base_cells, strict=True):
                if cell is None:
                    cells.append(None)
                elif any(s["from_date"] <= day["date"] <= s["to_date"] for s in segs):
                    cells.append(cell)
                else:
                    cells.append(_off_cell(cell, name))
        else:
            pid = get_user_person_id(db, user_id, on_date=real_today) or segs[-1]["person_id"]
            cells = []
            for day in days_in_week:
                seg_for_day = next((s for s in segs if s["from_date"] <= day["date"] <= s["to_date"]), None)
                cells.append(_cell_for(day, seg_for_day["person_id"]) if seg_for_day else None)

        merged_rows.append(
            {
                "person_id": pid,
                "person_name": name,
                "vacant": False,
                "holder_user_id": user_id,
                "cells": cells,
            }
        )

    person_rows = legacy_rows + sorted(merged_rows, key=lambda r: r["person_id"])

    if days_in_week:
        for entry in days_in_week[0].get("persons", []):
            sub_pid = entry.get("person_id")
            if isinstance(sub_pid, int) and 1 <= sub_pid <= 10:
                continue
            cells = [_cell_for(day, sub_pid) for day in days_in_week]
            person_rows.append(
                {
                    "person_id": sub_pid,
                    "person_name": entry.get("person_name", ""),
                    "vacant": False,
                    "is_substitute": True,
                    "substitute_id": entry.get("substitute_id"),
                    "cells": cells,
                }
            )

    return person_rows
```

Add the import at the top of `app/routes/schedule_all.py` (near the existing `from app.core.schedule.person_history import get_position_holder_segments, has_position_history` on line 30):

```python
from app.core.schedule.person_history import get_position_holder_segments, get_user_person_id, has_position_history
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python3 -m pytest tests/test_schedule_views_person_change.py -k "week" -v`
Expected: all week-related tests PASS, including the two new ones and every pre-existing week test (e.g. `test_mid_month_change_shows_both_persons` if it covers week view, and any other `week_all`-named test in the file).

- [ ] **Step 5: Run the full suite**

Run: `venv/bin/python3 -m pytest -q`
Expected: all tests pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/routes/schedule_all.py tests/test_schedule_views_person_change.py
git commit -m "fix(schedule): merge week view rows for position swaps, hide vacant rows"
```

---

### Task 2: Month view - merge swap participants into one column, hide fully-vacant columns

**Files:**
- Modify: `app/routes/schedule_all.py:194-318` (`show_month_all`)
- Test: `tests/test_schedule_views_person_change.py`

**Interfaces:**
- Consumes: `summarize_month_for_person(year, month, person_id, session, user_wages, year_days, fetch_tax_table, payment_year, wage_user_id) -> dict` (already exists, unchanged - has a `days` list plus aggregate fields like `total_ob`, `netto_pay`, `brutto_pay`; every field is `0`/falsy/empty for days masked out via `mask_days_to_employment`).
- Produces: a new module-level helper `_merge_month_summaries(summaries: list[dict]) -> dict` in `app/routes/schedule_all.py`, used only within `show_month_all`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_schedule_views_person_change.py`:

```python
def test_month_view_merges_swap_into_one_column(month_env):
    """A position swap landing mid-month yields ONE column per person.

    Anna (user 11) and Bert (user 12) hold positions 3 and 5 respectively,
    then swap on 2026-06-15 (mid-June). June's view must show each of them
    exactly once, with their correct shift on each side of the swap date.
    """
    client, session = month_env
    admin = _make_user(session, 2, "admin1", "Admin", role=UserRole.ADMIN)
    anna = _make_user(session, 11, "anna1", "Anna")
    bert = _make_user(session, 12, "bert1", "Bert")
    start_employment(session, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 2), created_by=admin.id)
    start_employment(session, bert.id, 5, "Bert", "bert1", datetime.date(2026, 1, 2), created_by=admin.id)
    swap_positions(session, 3, 5, datetime.date(2026, 6, 15), created_by=admin.id)

    token = create_access_token(data={"sub": str(admin.id)})
    client.cookies.set("access_token", f"Bearer {token}")
    resp = client.get("/month?year=2026&month=6")

    assert resp.status_code == 200
    assert resp.text.count(">Anna<") == 1
    assert resp.text.count(">Bert<") == 1


def test_month_view_hides_fully_vacant_position(month_env):
    """A position with no holder at all during the displayed month shows no column."""
    client, session = month_env
    admin = _make_user(session, 2, "admin1", "Admin", role=UserRole.ADMIN)
    isak = _make_user(session, 5, "isak1", "Isak")
    start_employment(session, isak.id, 5, "Isak", "isak1", datetime.date(2026, 1, 2), created_by=admin.id)
    end_employment(session, isak.id, datetime.date(2026, 8, 3), created_by=admin.id)

    token = create_access_token(data={"sub": str(admin.id)})
    client.cookies.set("access_token", f"Bearer {token}")
    resp = client.get("/month?year=2026&month=8")  # August: Isak leaves Aug 3, rest of month vacant

    assert resp.status_code == 200
    assert "Vacant" not in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python3 -m pytest tests/test_schedule_views_person_change.py -k "test_month_view_merges_swap_into_one_column or test_month_view_hides_fully_vacant_position" -v`
Expected: both FAIL (Anna/Bert each appear twice; "Vacant" appears in the August response).

- [ ] **Step 3: Add `_merge_month_summaries` and rewrite the per-position loop**

Add this helper function above `show_month_all` in `app/routes/schedule_all.py` (after `_build_person_rows`, before the `@router.get("/week"...)` line):

```python
def _merge_month_summaries(summaries: list[dict]) -> dict:
    """Combine date-disjoint month summaries for the same person into one.

    Each input summary was built from year_days masked to one segment's own
    date range via mask_days_to_employment (every day outside that segment is
    already zeroed to OFF with no pay). Segments for the same person during
    one month never overlap in time (PersonHistory allows only one open
    record per position), so merging is safe: take the first summary's shape,
    overlay any day from a later summary whose shift is not OFF, and sum
    every numeric aggregate field.
    """
    merged = dict(summaries[0])
    merged_days = list(summaries[0]["days"])
    for other in summaries[1:]:
        for i, other_day in enumerate(other["days"]):
            if other_day.get("shift") and other_day["shift"].code != "OFF":
                merged_days[i] = other_day
    merged["days"] = merged_days

    numeric_fields = [
        "total_hours",
        "num_shifts",
        "total_ob",
        "ob_pay",
        "netto_pay",
        "brutto_pay",
        "oncall_pay",
        "ot_pay",
    ]
    for field in numeric_fields:
        if field in merged and isinstance(merged.get(field), (int, float)):
            merged[field] = sum(s.get(field) or 0 for s in summaries)
    return merged
```

Replace the body of the `for pid in range(1, 11):` loop in `show_month_all` (lines 221-287) with:

```python
    persons = []
    for pid in range(1, 11):
        person_month_days = generate_month_data(year, month, pid, session=db, user_wages=user_wages)
        segments = get_position_holder_segments(db, pid, month_start, month_end)

        if not segments:
            if has_position_history(db, pid):
                continue  # Fully vacant for the whole month: no column.
            summary = summarize_month_for_person(
                year, month, pid, session=db, user_wages=user_wages,
                year_days=person_month_days, fetch_tax_table=is_admin, payment_year=year,
            )
            summary["holder_user_id"] = pid
            if not can_see_salary(current_user, pid):
                summary = strip_salary_data(summary)
            persons.append(summary)
            continue

        by_user: dict[int, list[dict]] = {}
        for seg in segments:
            by_user.setdefault(seg["user_id"], []).append(seg)

        for user_id, segs in by_user.items():
            per_segment_summaries = []
            for seg in segs:
                masked_days = mask_days_to_employment(person_month_days, seg["from_date"], seg["to_date"])
                s = summarize_month_for_person(
                    year, month, pid, session=db, user_wages=user_wages,
                    year_days=masked_days, fetch_tax_table=is_admin, payment_year=year,
                    wage_user_id=seg["user_id"],
                )
                s["person_name"] = seg["name"]
                per_segment_summaries.append(s)
            summary = per_segment_summaries[0] if len(per_segment_summaries) == 1 else _merge_month_summaries(
                per_segment_summaries
            )
            summary["person_name"] = segs[-1]["name"]
            summary["holder_user_id"] = user_id
            viewer_is_owner = current_user is not None and current_user.id == user_id
            if not (is_admin or viewer_is_owner):
                summary = strip_salary_data(summary)
            persons.append(summary)
```

Note: this changes the loop so a swap participant (segments across positions,
grouped by `user_id` regardless of which `pid` iteration they were fetched
under) is merged the first time their `user_id` is encountered under any
position; if the SAME `user_id` also holds segments under a later `pid` in
the outer loop, they would be appended a second time. To prevent this, track
already-emitted user ids across the whole outer loop: add
`emitted_user_ids: set[int] = set()` before the `for pid in range(1, 11):`
loop, `continue` in the inner `for user_id, segs in by_user.items():` loop if
`user_id in emitted_user_ids`, and add `emitted_user_ids.add(user_id)` right
before `persons.append(summary)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python3 -m pytest tests/test_schedule_views_person_change.py -k "month" -v`
Expected: all month-related tests pass, including the two new ones, `test_mid_month_change_shows_both_persons`, and every other existing `month_all`/`month` test in the file.

- [ ] **Step 5: Run the full suite**

Run: `venv/bin/python3 -m pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/routes/schedule_all.py tests/test_schedule_views_person_change.py
git commit -m "fix(schedule): merge month view columns for position swaps, hide vacant columns"
```

---

### Task 3: Year view - merge swap participants into one column, hide fully-vacant columns

**Files:**
- Modify: `app/routes/schedule_all.py:322-434` (`show_year_all`)
- Modify: `app/templates/year_all.html:101` (cell lookup)
- Test: `tests/test_schedule_views_person_change.py`

**Interfaces:**
- Consumes: `get_position_holder_segments`, `has_position_history`, `get_current_person_for_position`, `get_user_person_id` (all already imported/used in this file).
- Produces: `person_headers` list passed to the template gains, for merged (swap) entries only, a `position_by_date: dict[str, int]` key (ISO date string -> person_id). Non-merged entries are unchanged from today's shape.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_schedule_views_person_change.py`:

```python
def test_year_view_merges_swap_into_one_column(month_env):
    """A position swap between two active people yields ONE column per person
    in the year view, with the correct shift on each side of the swap date."""
    client, session = month_env
    admin = _make_user(session, 2, "admin1", "Admin", role=UserRole.ADMIN)
    rickard = _make_user(session, 11, "rickard1", "Rickard")
    okan = _make_user(session, 8, "okan1", "Okan")
    start_employment(session, rickard.id, 3, "Rickard", "rickard1", datetime.date(2026, 1, 2), created_by=admin.id)
    start_employment(session, okan.id, 8, "Okan", "okan1", datetime.date(2026, 1, 2), created_by=admin.id)
    swap_positions(session, 3, 8, datetime.date(2026, 10, 1), created_by=admin.id)

    token = create_access_token(data={"sub": str(admin.id)})
    client.cookies.set("access_token", f"Bearer {token}")
    resp = client.get("/year?year=2026")

    assert resp.status_code == 200
    assert resp.text.count(">Rickard<") == 1
    assert resp.text.count(">Okan<") == 1


def test_year_view_hides_fully_vacant_position(month_env):
    """A position with no holder at all during the displayed year shows no column."""
    client, session = month_env
    admin = _make_user(session, 2, "admin1", "Admin", role=UserRole.ADMIN)
    isak = _make_user(session, 5, "isak1", "Isak")
    start_employment(session, isak.id, 5, "Isak", "isak1", datetime.date(2026, 1, 2), created_by=admin.id)
    end_employment(session, isak.id, datetime.date(2026, 1, 31), created_by=admin.id)
    # No successor at all during 2026: position 5 vacant for 11 of 12 months.
    # (Still overlaps January, so this exercises the "held briefly" case, not
    # a full-year vacancy - see test_year_header_vacant_after_departure for
    # the existing partial-vacancy behavior, which must be unaffected.)

    token = create_access_token(data={"sub": str(admin.id)})
    client.cookies.set("access_token", f"Bearer {token}")
    resp = client.get("/year?year=2027")  # Position 5 has zero overlap with 2027 at all

    assert resp.status_code == 200
    assert "Vacant" not in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python3 -m pytest tests/test_schedule_views_person_change.py -k "test_year_view_merges_swap_into_one_column or test_year_view_hides_fully_vacant_position" -v`
Expected: both FAIL.

- [ ] **Step 3: Rewrite the `person_headers` build loop in `show_year_all`**

Replace the `for pid in range(1, 11):` loop (lines 369-434) with:

```python
    person_headers = []
    legacy_pids_seen = set()
    segments_by_user: dict[int, list[dict]] = {}
    for pid in range(1, 11):
        segments = get_position_holder_segments(db, pid, year_start, year_end)
        merged: list[dict] = []
        for seg in segments:
            if merged and merged[-1]["user_id"] == seg["user_id"]:
                merged[-1]["to_date"] = seg["to_date"]
            else:
                merged.append(dict(seg))

        if not merged:
            if has_position_history(db, pid):
                continue  # Fully vacant for the whole year: no column.
            cp = get_current_person_for_position(db, pid)
            person_headers.append(
                {
                    "person_id": pid,
                    "user_id": pid,
                    "name": cp["name"] if cp else f"Person {pid}",
                    "vacant": False,
                    "col_key": f"{pid}-{pid}",
                    "from_date": year_start,
                    "to_date": year_end,
                    "past": False,
                    "future": False,
                }
            )
            continue

        for seg in merged:
            segments_by_user.setdefault(seg["user_id"], []).append({**seg, "person_id": pid})

    for user_id, segs in segments_by_user.items():
        segs.sort(key=lambda s: s["effective_from"])
        positions_held = {s["person_id"] for s in segs}

        if len(positions_held) == 1:
            seg = segs[0]
            to_date = seg["to_date"]
            from_date = seg["from_date"]
            past = to_date is not None and to_date < real_today
            future = seg["effective_from"] > real_today
            person_headers.append(
                {
                    "person_id": seg["person_id"],
                    "user_id": user_id,
                    "name": seg["name"],
                    "vacant": False,
                    "col_key": f"{seg['person_id']}-{user_id}",
                    "from_date": from_date,
                    "to_date": to_date,
                    "past": past,
                    "future": future,
                }
            )
        else:
            current_pid = get_user_person_id(db, user_id, on_date=real_today) or segs[-1]["person_id"]
            position_by_date: dict[str, int] = {}
            d = min(s["from_date"] for s in segs)
            end = max(s["to_date"] or year_end for s in segs)
            while d <= end:
                seg_for_day = next((s for s in segs if s["from_date"] <= d <= (s["to_date"] or year_end)), None)
                if seg_for_day:
                    position_by_date[d.isoformat()] = seg_for_day["person_id"]
                d += timedelta(days=1)
            person_headers.append(
                {
                    "person_id": current_pid,
                    "user_id": user_id,
                    "name": segs[-1]["name"],
                    "vacant": False,
                    "col_key": f"user-{user_id}",
                    "from_date": min(s["from_date"] for s in segs),
                    "to_date": max(s["to_date"] for s in segs) if all(s["to_date"] for s in segs) else None,
                    "past": False,
                    "future": False,
                    "position_by_date": position_by_date,
                }
            )

    person_headers.sort(key=lambda h: h["person_id"])
```

Add `from datetime import timedelta` to the existing `from datetime import date` import line at the top of `app/routes/schedule_all.py` if not already present (check line 7 - it currently imports `date, datetime, timedelta`, so this is likely already available; verify before adding a duplicate import).

- [ ] **Step 4: Update the template cell lookup**

In `app/templates/year_all.html`, find line 101:

```jinja
{% set person = (day.persons | selectattr('person_id', 'equalto', col.person_id) | list | first) %}
```

Replace with:

```jinja
{% set effective_pid = col.position_by_date[day.date.isoformat()] if col.position_by_date is defined and day.date.isoformat() in col.position_by_date else col.person_id %}
{% set person = (day.persons | selectattr('person_id', 'equalto', effective_pid) | list | first) %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/python3 -m pytest tests/test_schedule_views_person_change.py -k "year" -v`
Expected: all year-related tests pass, including the two new ones and every pre-existing year test in the file (`test_year_header_vacant_after_departure`, `test_year_splits_columns_per_holder_past_hidden`, `test_year_future_swap_columns_hidden_until_effective`, `test_year_ongoing_holder_visible_in_later_year`, `test_year_out_of_tenure_cells_render_off`, `test_year_summary_filters_to_viewed_users_employment`, `test_year_by_user_id_shows_old_holder`, `test_year_summary_spans_position_swap`, `test_year_summary_stitches_mid_month_position_move`, `test_year_summary_counts_user_keyed_absences`, `test_year_totals_api_rejects_foreign_user_id`, `test_year_redirects_non_owner_non_admin`).

- [ ] **Step 6: Run the full suite**

Run: `venv/bin/python3 -m pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/routes/schedule_all.py app/templates/year_all.html tests/test_schedule_views_person_change.py
git commit -m "fix(schedule): merge year view columns for position swaps, hide vacant columns"
```

---

### Task 4: Redirect departed users away from post-tenure `/month/{id}`

**Files:**
- Modify: `app/routes/schedule_personal.py` (`show_month_for_person`, starting line 761 as of branch base)
- Test: `tests/test_schedule_views_person_change.py`

**Interfaces:**
- Consumes: `get_employment_period(db, user_id, rotation_position) -> tuple[date, date | None]` (already imported and used elsewhere in this file).
- Produces: no new functions; adds an early `RedirectResponse` inside the existing route.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_schedule_views_person_change.py`:

```python
def test_month_redirects_departed_user_to_team_view(month_env):
    """A departed user's personal month view redirects to month_all once the
    ENTIRE requested month is after their own last working day."""
    client, session = month_env
    admin = _make_user(session, 2, "admin1", "Admin", role=UserRole.ADMIN)
    robin = _make_user(session, 10, "robin1", "Robin")
    start_employment(session, robin.id, 10, "Robin", "robin1", datetime.date(2026, 1, 2), created_by=admin.id)
    end_employment(session, robin.id, datetime.date(2026, 3, 31), created_by=admin.id)

    token = create_access_token(data={"sub": str(robin.id)})
    client.cookies.set("access_token", f"Bearer {token}")

    # July is entirely after his own tenure: redirect.
    resp = client.get("/month/10?year=2026&month=7", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/month?year=2026&month=7"

    # March is his own real last month: no redirect, renders normally.
    resp2 = client.get("/month/10?year=2026&month=3", follow_redirects=False)
    assert resp2.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_schedule_views_person_change.py -k test_month_redirects_departed_user_to_team_view -v`
Expected: FAILS on the first assertion (`resp.status_code == 200`, not `302` - the page currently renders an all-OFF month instead of redirecting).

- [ ] **Step 3: Add the redirect**

In `app/routes/schedule_personal.py`, inside `show_month_for_person`, immediately after the existing block that computes `viewer_employment_start`/`viewer_employment_end` (search for `viewer_employment_start = emp_start` - this was added in commit `313a7df`; the same block also has `emp_end`), add:

```python
    if target_user is not None and viewer_employment_end is not None:
        from calendar import monthrange

        month_start = date(year, month, 1)
        if month_start > viewer_employment_end:
            return RedirectResponse(url=f"/month?year={year}&month={month}", status_code=302)
```

Place this check after `viewer_employment_start`/`viewer_employment_end` are both known but before `calendar_data = build_calendar_grid_for_month(...)` is called, so no unnecessary work happens before redirecting. Confirm `RedirectResponse` is already imported at the top of the file (it is, per the existing `from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse` import).

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_schedule_views_person_change.py -k test_month_redirects_departed_user_to_team_view -v`
Expected: PASSES.

- [ ] **Step 5: Run the full suite**

Run: `venv/bin/python3 -m pytest -q`
Expected: all tests pass, including every existing `/month/{id}` test (e.g. `test_year_by_user_id_shows_old_holder`'s sibling month tests, `test_month_view_shows_swap_padding_days_from_next_month`, `test_month_view_shows_swap_padding_days_from_prev_month`) - these must still render normally since their requested months are NOT entirely after the viewed user's own tenure end.

- [ ] **Step 6: Commit**

```bash
git add app/routes/schedule_personal.py tests/test_schedule_views_person_change.py
git commit -m "fix(schedule): redirect departed user's post-tenure month view to team view"
```

---

### Task 5: Redirect departed users away from post-tenure `/week/{id}`

**Files:**
- Modify: `app/routes/schedule_personal.py` (`show_week_for_person`, starting line 543 as of branch base)
- Test: `tests/test_schedule_views_person_change.py`

**Interfaces:**
- Consumes: `get_employment_period` (same as Task 4).
- Produces: no new functions; adds an early `RedirectResponse` inside the existing route.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_schedule_views_person_change.py`:

```python
def test_week_redirects_departed_user_to_team_view(month_env):
    """A departed user's personal week view redirects to week_all once the
    ENTIRE requested week is after their own last working day."""
    client, session = month_env
    admin = _make_user(session, 2, "admin1", "Admin", role=UserRole.ADMIN)
    robin = _make_user(session, 10, "robin1", "Robin")
    start_employment(session, robin.id, 10, "Robin", "robin1", datetime.date(2026, 1, 2), created_by=admin.id)
    end_employment(session, robin.id, datetime.date(2026, 3, 31), created_by=admin.id)

    token = create_access_token(data={"sub": str(robin.id)})
    client.cookies.set("access_token", f"Bearer {token}")

    # Week 30 (late July) is entirely after his tenure: redirect.
    resp = client.get("/week/10?year=2026&week=30", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/week?year=2026&week=30"

    # Week 1 (his own real first week) still renders normally.
    resp2 = client.get("/week/10?year=2026&week=1", follow_redirects=False)
    assert resp2.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_schedule_views_person_change.py -k test_week_redirects_departed_user_to_team_view -v`
Expected: FAILS on the first assertion.

- [ ] **Step 3: Add the redirect**

`show_week_for_person` already computes everything needed: `monday` (line 565: `monday = date.fromisocalendar(year, week, 1)`) and `week_employment_end` (set from `get_employment_period(db, target_user.id, rotation_position)` a few lines below). Insert the redirect check right after the block that sets `week_employment_start`/`week_employment_end` (the `if target_user is not None:` block that calls `get_employment_period`), before the `days_in_week = build_week_data(...)` call:

```python
    if target_user is not None and week_employment_end is not None and monday > week_employment_end:
        return RedirectResponse(url=f"/week?year={year}&week={week}", status_code=302)
```

`RedirectResponse` is already imported at the top of `app/routes/schedule_personal.py` (`from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse`), so no new import is needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_schedule_views_person_change.py -k test_week_redirects_departed_user_to_team_view -v`
Expected: PASSES.

- [ ] **Step 5: Run the full suite**

Run: `venv/bin/python3 -m pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/routes/schedule_personal.py tests/test_schedule_views_person_change.py
git commit -m "fix(schedule): redirect departed user's post-tenure week view to team view"
```

---

## Final check

- [ ] Run `venv/bin/python3 -m pytest -q` one more time after all five tasks - full suite green.
- [ ] Run `ruff check .` and `ruff format --check .` - both clean.
- [ ] Do NOT push or open a PR - leave all five commits local on `feat/person-centric-columns` for review.
