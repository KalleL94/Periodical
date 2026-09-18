# Per-day Values Implementation Plan (branch 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each selected day its own values, for example sick on Monday, child care on Wednesday, overtime on Friday, in one submit.

**Architecture:** A fifth drawer tab renders one row per selected day. An area select above the table chooses Frånvaro, Tid or Pass and only that area's columns are visible, which is what keeps a row usable at 400 px. Rows are cloned in the browser from a hidden `<template>`, because the drawer renders before anything is selected. One new route, `/day-edit/bulk`, reads the fields belonging to the posted area.

**Tech Stack:** FastAPI, Jinja2, vanilla JS. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-12-day-time-segments-design.md`, section "Branch 3".

**Depends on:** `feat/multiday-edit`. `_day_edit_panel.html`, the `drawer` flag, `day-selection-change`, `apply_to_dates`, `MAX_EDIT_DATES` and `edit_redirect_url` must exist.

## Global Constraints

- **Branch from `feat/multiday-edit`.** Four parents are unmerged.
- **Only the posted area's fields are read.** Switching area leaves the other two field sets in the DOM and they still submit. Acting on them would write values the user never looked at. This is the single most important rule here.
- **Rows are built client-side.** Do not add a GET endpoint to prefill the table, and do not make selecting a day cost a round trip.
- **No prefill of current values.** Removal is covered by `/day-edit/clear`. Adding prefill is a separate decision, not a "while I am here".
- **400 px is a hard requirement,** not a nice-to-have: it is why the design is one area at a time. Verify it.
- **Every user-facing string goes in `app/core/translations.py`, both languages.**
- **All code comments in English. No em dash (U+2014) anywhere,** including commit messages.
- **Run the full suite in the FOREGROUND** with a 600000 ms timeout; background runs get killed by a memory guard. Baseline before you start: `1152 passed, 32 skipped`.

---

### Task 1: The bulk route

**Files:**
- Modify: `app/routes/day_edit.py`
- Test: `tests/test_day_edit_bulk.py`

**Interfaces:**
- Produces: `POST /day-edit/bulk`, reading the raw form. Required fields: `user_id`, `area` (`absence` | `time` | `shift`), `return_to`, and per date `<area>_<field>_<ISO date>`.
  - `absence`: `absence_type`, `absence_arrived`, `absence_left`
  - `time`: `time_kind`, `time_side`, `time_start`, `time_end`, `time_hours`
  - `shift`: `shift_code`, `shift_label`, `shift_start`, `shift_end`
- Produces: a date whose area fields are all blank is skipped, not written. Selecting seven days and filling three must write three rows, not seven empty ones.

Reuse the existing writers rather than duplicating them. `add_overtime_shift`, `add_absence` and `add_shift_override` each already upsert one date; factor the body of each into a module-level `_upsert_*(session, user_id, date, ...)` helper in its own route module and call that from both places. Do not copy the upsert logic into `day_edit.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_day_edit_bulk.py`, using the `test_db` / `test_user` fixtures the other route tests use.

```python
import datetime

import pytest

from app.database.database import Absence, AbsenceType, OvertimeShift, ShiftOverride
from app.routes.day_edit import bulk_edit

D = datetime.date
DATES = [D(2026, 6, 1), D(2026, 6, 2), D(2026, 6, 3)]


def _form(area, user_id, rows, return_to=""):
    """The flat field shape the browser posts: <area>_<field>_<ISO date>."""
    data = {"user_id": str(user_id), "area": area, "return_to": return_to}
    for date, fields in rows.items():
        for name, value in fields.items():
            data[f"{name}_{date.isoformat()}"] = value
    data["dates"] = [d.isoformat() for d in DATES]
    return data


@pytest.mark.anyio
async def test_each_day_gets_its_own_absence_type(test_db, test_user, bulk_request):
    request = bulk_request(
        _form("absence", test_user.id, {
            DATES[0]: {"absence_type": "SICK", "absence_arrived": "", "absence_left": ""},
            DATES[1]: {"absence_type": "VAB", "absence_arrived": "", "absence_left": ""},
            DATES[2]: {"absence_type": "", "absence_arrived": "", "absence_left": ""},
        })
    )
    await bulk_edit(request=request, session=test_db, current_user=test_user)

    rows = {a.date: a.absence_type for a in test_db.query(Absence).all()}
    assert rows == {DATES[0]: AbsenceType.SICK, DATES[1]: AbsenceType.VAB}


@pytest.mark.anyio
async def test_a_blank_row_writes_nothing(test_db, test_user, bulk_request):
    request = bulk_request(
        _form("absence", test_user.id, {d: {"absence_type": "", "absence_arrived": "", "absence_left": ""} for d in DATES})
    )
    await bulk_edit(request=request, session=test_db, current_user=test_user)
    assert test_db.query(Absence).count() == 0


@pytest.mark.anyio
async def test_only_the_posted_area_is_read(test_db, test_user, bulk_request):
    """Switching tab leaves the other areas' fields in the DOM; they still submit."""
    form = _form("absence", test_user.id, {
        DATES[0]: {"absence_type": "SICK", "absence_arrived": "", "absence_left": ""},
    })
    form[f"time_kind_{DATES[0].isoformat()}"] = "extra"
    form[f"time_side_{DATES[0].isoformat()}"] = "before"
    form[f"time_start_{DATES[0].isoformat()}"] = "05:00"
    form[f"time_end_{DATES[0].isoformat()}"] = "06:00"
    form[f"time_hours_{DATES[0].isoformat()}"] = "1.0"

    await bulk_edit(request=bulk_request(form), session=test_db, current_user=test_user)

    assert test_db.query(Absence).count() == 1
    assert test_db.query(OvertimeShift).count() == 0


@pytest.mark.anyio
async def test_each_day_gets_its_own_time_row(test_db, test_user, bulk_request):
    request = bulk_request(
        _form("time", test_user.id, {
            DATES[0]: {"time_kind": "extra", "time_side": "before", "time_start": "05:00",
                       "time_end": "06:00", "time_hours": "1.0"},
            DATES[1]: {"time_kind": "ot", "time_side": "after", "time_start": "22:30",
                       "time_end": "00:30", "time_hours": "2.0"},
            DATES[2]: {"time_kind": "", "time_side": "", "time_start": "", "time_end": "", "time_hours": ""},
        })
    )
    await bulk_edit(request=request, session=test_db, current_user=test_user)

    rows = {r.date: (r.kind, r.side, r.hours) for r in test_db.query(OvertimeShift).all()}
    assert rows == {DATES[0]: ("extra", "before", 1.0), DATES[1]: ("ot", "after", 2.0)}


@pytest.mark.anyio
async def test_each_day_gets_its_own_shift_code(test_db, test_user, bulk_request):
    request = bulk_request(
        _form("shift", test_user.id, {
            DATES[0]: {"shift_code": "N1", "shift_label": "", "shift_start": "", "shift_end": ""},
            DATES[1]: {"shift_code": "ETC", "shift_label": "Kurs", "shift_start": "09:00", "shift_end": "12:00"},
            DATES[2]: {"shift_code": "", "shift_label": "", "shift_start": "", "shift_end": ""},
        })
    )
    await bulk_edit(request=request, session=test_db, current_user=test_user)

    rows = {o.date: (o.shift_code, o.label) for o in test_db.query(ShiftOverride).all()}
    assert rows == {DATES[0]: ("N1", None), DATES[1]: ("ETC", "Kurs")}


@pytest.mark.anyio
async def test_an_unknown_area_is_rejected(test_db, test_user, bulk_request):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await bulk_edit(request=bulk_request(_form("nonsense", test_user.id, {})),
                        session=test_db, current_user=test_user)
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_editing_another_user_is_refused(test_db, test_user, bulk_request):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await bulk_edit(request=bulk_request(_form("absence", test_user.id + 999, {})),
                        session=test_db, current_user=test_user)
    assert exc.value.status_code == 403
```

Add the `bulk_request` fixture to the same file. The route reads the raw form, so the test needs a Request whose `form()` returns the dict:

```python
@pytest.fixture
def bulk_request():
    """A Request stand-in whose form() returns the given dict.

    The route reads the raw form because the field names carry their date, which
    is what keeps rows from relying on parallel arrays staying aligned.
    """

    def build(data):
        class _Request:
            async def form(self):
                return data

        return _Request()

    return build
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_day_edit_bulk.py -q`
Expected: `ImportError: cannot import name 'bulk_edit'`.

- [ ] **Step 3: Factor the three upserts out of their routes**

In `app/routes/overtime.py`, `app/routes/profile.py` and `app/routes/shift_override.py`, lift the per-date write out of each handler's `write()` closure into a module-level function, and call it from the closure. Signatures:

```python
def upsert_overtime(session, user_id, date, start_time, end_time, hours, kind, side, created_by, ot_pay):
def upsert_absence(session, user_id, date, absence_type, left_at, arrived_at):
def upsert_shift_override(session, user_id, date, shift_code, start_time, end_time, label, created_by):
```

Each keeps the exact behaviour it has now, including the upsert key. Run the suite after this step alone: it must be unchanged at `1152 passed, 32 skipped`. Committing this refactor separately is what makes Task 1's real change reviewable.

- [ ] **Step 4: Write the route**

Add to `app/routes/day_edit.py`:

```python
_AREAS = {
    "absence": ("absence_type", "absence_arrived", "absence_left"),
    "time": ("time_kind", "time_side", "time_start", "time_end", "time_hours"),
    "shift": ("shift_code", "shift_label", "shift_start", "shift_end"),
}


def _rows_for_area(form, area: str) -> dict[date_cls, dict[str, str]]:
    """Group `<area>_<field>_<ISO date>` fields by date.

    Only this area's fields are read. Switching tab leaves the other two sets in
    the DOM and they still submit, so reading them would write values the user
    never looked at.
    """
    rows: dict[date_cls, dict[str, str]] = {}
    for field in _AREAS[area]:
        prefix = f"{field}_"
        for key, value in form.items():
            if not key.startswith(prefix):
                continue
            try:
                day = date_cls.fromisoformat(key[len(prefix) :])
            except ValueError:
                continue
            rows.setdefault(day, {})[field] = (value or "").strip()
    # A row the user left alone writes nothing.
    return {day: fields for day, fields in rows.items() if any(fields.values())}
```

The handler validates `area` and `user_id` the way `clear_days` does, raises 400 past `MAX_EDIT_DATES` rows, dispatches each row to the matching `upsert_*`, commits once, calls `clear_schedule_cache()` once, and redirects through `edit_redirect_url`.

Compute overtime pay the way `/overtime/add` does, through the same rate lookup, and store `0.0` for `kind == "extra"`.

- [ ] **Step 5: Run the tests and commit**

```bash
venv/bin/python3 -m pytest tests/test_day_edit_bulk.py -q
venv/bin/python3 -m pytest -q -p no:cacheprovider 2>&1 | tail -3
venv/bin/python3 -m ruff check . && venv/bin/python3 -m ruff format --check .
git add -A
git commit -m "$(cat <<'MSG'
feat(routes): write a different value per day in one post

The drawer applies one value to every selected day. This is the case it cannot
express: sick on Monday, child care on Wednesday, overtime on Friday.

Field names carry their date, so the route groups by suffix rather than trusting
parallel arrays to stay aligned, and a row left blank writes nothing. Only the
posted area's fields are read: switching tab leaves the other two sets in the
DOM, and acting on them would write values nobody looked at.

The three upserts are called from their own route modules, not copied here.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MgsRgKYs94sawwQ12fmxMC
MSG
)"
```

---

### Task 2: The Per dag tab

**Files:**
- Modify: `app/templates/_day_edit_panel.html`
- Modify: `app/static/js/day-edit-panel.js`
- Modify: `app/static/css/components.css`
- Modify: `app/core/translations.py`
- Test: `tests/test_multiday_selection.py` (extend)

**Interfaces:**
- Consumes: `day-selection-change` from `day-selection.js`.
- Produces: a fifth tab `tab-perdag`, drawer-only, holding an area select, a `<template id="per-day-row">` and an empty `<tbody id="per-day-rows">` the script fills.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("view", sorted(VIEWS))
def test_the_drawer_offers_a_per_day_tab(env, view):
    client, _ = env
    html = client.get(VIEWS[view]).text
    drawer = html[html.index('id="day-edit-drawer"') :]
    assert 'id="tab-perdag"' in drawer
    assert 'id="per-day-row"' in drawer
    assert 'action="/day-edit/bulk"' in drawer


def test_the_day_page_has_no_per_day_tab(env):
    """One day does not need a per-day table."""
    client, _ = env
    html = client.get(f"/day/1/{DAY.year}/{DAY.month}/{DAY.day}").text
    assert 'id="tab-perdag"' not in html
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_multiday_selection.py -q`

- [ ] **Step 3: Add the tab button**

In the tab row, inside a `{% if drawer %}` guard so the day page never shows it:

```jinja
        {% if drawer %}<button type="button" class="day-tab" data-tab="perdag" role="tab">{{ t.day_tab_perday }}</button>{% endif %}
```

- [ ] **Step 4: Add the panel**

After `</div>{# /tab-byte #}`, still inside the drawer's `{% if drawer %}`:

```jinja
    <div class="day-tab-panel" id="tab-perdag">
        <form method="POST" action="/day-edit/bulk" class="ot-form" id="per-day-form">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <input type="hidden" name="user_id" value="{{ person_id }}">
            <input type="hidden" name="return_to" value="{{ return_to_url }}">

            <div class="form-row">
                <div class="form-group">
                    <label for="per_day_area">{{ t.day_perday_area }}</label>
                    {# The area decides which columns are visible and which fields the
                       route reads. The other two stay in the DOM and still submit,
                       which is why the route ignores them. #}
                    <select id="per_day_area" name="area">
                        <option value="absence">{{ t.day_tab_absence }}</option>
                        <option value="time">{{ t.day_tab_time }}</option>
                        <option value="shift">{{ t.day_tab_swap }}</option>
                    </select>
                </div>
            </div>

            <table class="keep-table per-day-table is-area-absence" id="per-day-table">
                <tbody id="per-day-rows"></tbody>
            </table>

            <button type="submit" class="btn btn-primary">{{ t.day_perday_save }}</button>
        </form>
        <p class="info-text">{{ t.day_perday_hint }}</p>
    </div>

    {# Cloned once per selected day. Names are empty here so the template never
       submits; the script sets each to <field>_<ISO date> when it clones. Every
       cell carries data-label, which is what tables.css turns into the phone
       card layout below 800 px. #}
    <template id="per-day-row">
        <tr>
            <td class="per-day-date" data-label="{{ t.day_perday_day }}"></td>
            <td class="per-day-area per-day-area--absence" data-label="{{ t.day_absence_type_label }}">
                <select data-field="absence_type">
                    <option value="">-</option>
                    <option value="SICK">{{ t.day_absence_sick }}</option>
                    <option value="VAB">{{ t.day_absence_vab }}</option>
                    <option value="LEAVE">{{ t.day_absence_leave }}</option>
                    <option value="OFF">{{ t.day_absence_off }}</option>
                </select>
            </td>
            <td class="per-day-area per-day-area--absence" data-label="{{ t.day_absence_arrived_at_label }}">
                <input type="time" data-field="absence_arrived">
            </td>
            <td class="per-day-area per-day-area--absence" data-label="{{ t.day_absence_left_at_label }}">
                <input type="time" data-field="absence_left">
            </td>

            <td class="per-day-area per-day-area--time" data-label="{{ t.day_seg_type }}">
                <select data-field="time_kind">
                    <option value="">-</option>
                    <option value="ot">{{ t.day_seg_kind_ot }}</option>
                    <option value="extra">{{ t.day_seg_kind_extra }}</option>
                </select>
            </td>
            <td class="per-day-area per-day-area--time" data-label="{{ t.day_seg_when }}">
                <select data-field="time_side">
                    <option value="after">{{ t.day_seg_side_after }}</option>
                    <option value="before">{{ t.day_seg_side_before }}</option>
                    <option value="full">{{ t.day_seg_side_full }}</option>
                </select>
            </td>
            <td class="per-day-area per-day-area--time" data-label="{{ t.day_start_time }}">
                <input type="time" data-field="time_start">
            </td>
            <td class="per-day-area per-day-area--time" data-label="{{ t.day_end_time }}">
                <input type="time" data-field="time_end">
            </td>
            <td class="per-day-area per-day-area--time" data-label="{{ t.day_ext_hours }}">
                <input type="number" step="0.01" min="0" data-field="time_hours">
            </td>

            <td class="per-day-area per-day-area--shift" data-label="{{ t.day_shift_override_label }}">
                <select data-field="shift_code">
                    <option value="">-</option>
                    <option value="N1">N1</option>
                    <option value="N2">N2</option>
                    <option value="N3">N3</option>
                    <option value="ETC">{{ t.day_shift_etc }}</option>
                </select>
            </td>
            <td class="per-day-area per-day-area--shift" data-label="{{ t.day_shift_etc_label }}">
                <input type="text" maxlength="40" data-field="shift_label">
            </td>
            <td class="per-day-area per-day-area--shift" data-label="{{ t.day_start_time }}">
                <input type="time" data-field="shift_start">
            </td>
            <td class="per-day-area per-day-area--shift" data-label="{{ t.day_end_time }}">
                <input type="time" data-field="shift_end">
            </td>
        </tr>
    </template>
```

The column groups are shown one at a time by a class on the table, the same switch the tabs already use. Add to `components.css`:

```css
/* Per-day table: one area's columns at a time. Twelve controls per row is not a
   usable table at phone width, which is what drives the whole design. */
.per-day-area { display: none; }
.per-day-table.is-area-absence .per-day-area--absence,
.per-day-table.is-area-time .per-day-area--time,
.per-day-table.is-area-shift .per-day-area--shift { display: table-cell; }
@media (max-width: 800px) {
  .per-day-table.is-area-absence .per-day-area--absence,
  .per-day-table.is-area-time .per-day-area--time,
  .per-day-table.is-area-shift .per-day-area--shift { display: block; }
}
```

The media query repeats the selector because `tables.css` turns cells into blocks below 800 px, and `display: table-cell` would override that.

- [ ] **Step 5: Fill the table from the selection**

Add to `app/static/js/day-edit-panel.js`:

```javascript
    // Per-day rows are built here, not on the server: the drawer renders before
    // anything is selected, so the server never knows which days the table needs.
    var rowTemplate = document.getElementById('per-day-row');
    var rowBody = document.getElementById('per-day-rows');
    var areaSelect = document.getElementById('per_day_area');
    var perDayTable = document.getElementById('per-day-table');

    if (rowTemplate && rowBody && areaSelect && perDayTable) {
        var weekday = function (iso) {
            var d = new Date(iso + 'T00:00:00');
            return d.toLocaleDateString(document.documentElement.lang || 'sv', {
                weekday: 'short', day: 'numeric', month: 'short'
            });
        };

        document.addEventListener('day-selection-change', function (event) {
            rowBody.textContent = '';
            event.detail.dates.forEach(function (iso) {
                var row = rowTemplate.content.cloneNode(true);
                row.querySelector('.per-day-date').textContent = weekday(iso);
                row.querySelectorAll('[data-field]').forEach(function (el) {
                    el.name = el.dataset.field + '_' + iso;
                });
                rowBody.appendChild(row);
            });
        });

        areaSelect.addEventListener('change', function () {
            ['absence', 'time', 'shift'].forEach(function (area) {
                perDayTable.classList.toggle('is-area-' + area, areaSelect.value === area);
            });
        });
    }
```

New keys, both languages: `day_tab_perday` (`Per dag` / `Per day`), `day_perday_area` (`Område` / `Area`), `day_perday_day` (`Dag` / `Day`), `day_perday_save` (`Spara raderna` / `Save the rows`), `day_perday_hint` (`Tomma rader skrivs inte. Bara det valda området sparas.` / `Blank rows are not written. Only the selected area is saved.`).

- [ ] **Step 6: Run, then commit**

```bash
venv/bin/python3 -m pytest tests/test_multiday_selection.py tests/test_day_edit_panel.py -q
venv/bin/python3 -m ruff check . && venv/bin/python3 -m ruff format --check .
git add -A
git commit -m "$(cat <<'MSG'
feat(calendar): a per-day tab in the multi-day drawer

One row per selected day, with an area select above it so a row stays three to
five fields wide. All three areas at once is twelve controls per row, which is
not a usable table at phone width.

Rows are cloned in the browser from a template, because the drawer renders
before anything is selected. That is also why the table does not prefill with
each day's current values: it would cost a round trip, and removal is already
covered by the clear form.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MgsRgKYs94sawwQ12fmxMC
MSG
)"
```

---

### Task 3: Nothing to do

`app/static/css/tables.css:69` already turns every `tbody td` into a labelled
block below 800 px, using `content: attr(data-label)`. The per-day table inherits
that for free, so there is no new CSS to write.

The requirement it places on Task 2 is concrete: **every `<td>` in the row
template needs a `data-label`**, or the card layout renders unlabelled fields.
Verify at 400 px in Task 4.

---

### Task 4: Verify in a browser

Use the `browse` skill with `GSTACK_CHROMIUM_NO_SANDBOX=1` on the call that starts the daemon.

- [ ] **Step 1: Different values per day**

Log in as `ddf412`, open a month, select three days, open Per dag, set day 1 to SICK, day 2 to VAB, leave day 3 blank, submit. Confirm exactly two absences with the right types:

```bash
sqlite3 app/database/schedule.db "SELECT date, absence_type FROM absences WHERE user_id=6 AND date LIKE '2026-12%' ORDER BY date;"
```

- [ ] **Step 2: The other area does not leak**

Select three days, fill the Time columns, switch the area select to Frånvaro without clearing them, submit. Expected: no overtime rows written. This is the rule from the Global Constraints, checked end to end.

- [ ] **Step 3: 400 px**

`viewport 400x800`, select three days, open Per dag, confirm each row reads as a labelled card and `document.documentElement.scrollWidth === window.innerWidth`.

- [ ] **Step 4: Clean up and report**

Delete the rows written, restart the container, and report the full suite count, the traceback count, and what the success alert said.

---

## What this branch does not do

No prefill of each day's current values. That needs a round trip or a JSON endpoint, and `/day-edit/clear` already covers removal.

No mixing areas in one submit. Pick Frånvaro, Tid or Pass, submit, pick another.
