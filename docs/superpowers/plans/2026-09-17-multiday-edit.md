# Multi-day Edit Implementation Plan (branch 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change several days at once by selecting them in the month or week view, instead of visiting one day page per day.

**Architecture:** Branch 1b already did the hard part: the four edit routes take `dates` as a list, skip conflicting dates when more than one is posted, and redirect to a validated `return_to`. This branch adds only the surface. `td.calendar-day` gains `data-date`, one script in `app/static/` handles selection, and `_day_edit_panel.html` renders once per view against the selection. No route changes beyond a dates cap, and no pay code at all.

**Tech Stack:** Jinja2, vanilla JS, existing CSS tokens. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-12-day-time-segments-design.md`

**Depends on:** `feat/day-edit-panel`. `_day_edit_panel.html`, `edit_dates`, `apply_to_dates` and `edit_redirect_url` must exist before Task 2.

## Global Constraints

- **Branch from `feat/day-edit-panel`, not `main`.** All three parents are unmerged.
- **No new routes and no new pay code.** If a task tempts you to touch `app/core/schedule/`, stop: the requirement is already met there.
- **`month.html` and `week.html` use identical cell markup.** One script and one CSS block serve both. Writing two of anything here is the mistake this branch exists to avoid.
- **The day number keeps its link.** Selection is the rest of the cell, so clicking the date still opens the day page. A calendar you cannot navigate is a regression.
- **No inline event handlers and no inline styles.** Follow `day.html`'s pattern: a `<script>` block that binds listeners, and CSS classes toggled by `classList`.
- **Every user-facing string goes in `app/core/translations.py`, both the Swedish and the English block.**
- **All code comments in English. No em dash (U+2014) anywhere,** including commit messages.
- **Run from the repo root** using `venv/bin/python3`. Run the full suite in the FOREGROUND with a 600000 ms timeout; background runs get killed by a memory guard on this machine. Baseline to match before you start: `1123 passed, 32 skipped`.

---

### Task 1: Make the skip report visible

**Files:**
- Modify: `app/core/helpers.py` (`result_param`)
- Modify: `app/routes/schedule_personal.py` (day, week and month routes)
- Modify: `app/templates/day.html`, `app/templates/week.html`, `app/templates/month.html`
- Modify: `app/core/translations.py`
- Test: `tests/test_multidate_edit_routes.py` (extend)

**Interfaces:**
- Produces: `result_param` emits `?success=` instead of `?result=`, reusing the query parameter and the `alert alert--success` markup that `admin_users.py:30` and `admin_users.html:8` already establish.
- Produces: the day, week and month routes accept `success: str | None = Query(None)` and pass it to the template.

Branch 1b builds a skip report into the redirect and **nothing renders it**. Until this task lands, "1 hoppades över" is invisible, so a multi-day post looks like it silently did nothing to that day. Do this first: it is the difference between a reported skip and a mystery.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_multidate_edit_routes.py`:

```python
@pytest.mark.anyio
async def test_the_skip_report_uses_the_success_parameter(test_db, test_user):
    """The views already render ?success=; a bespoke ?result= would render nowhere."""
    test_db.add(
        OvertimeShift(
            user_id=test_user.id,
            date=D(2026, 6, 2),
            start_time=datetime.time(9, 0),
            end_time=datetime.time(10, 0),
            hours=1.0,
            ot_pay=0.0,
            kind="extra",
            side="before",
        )
    )
    test_db.commit()

    response = await add_overtime_shift(
        user_id=test_user.id,
        dates=[D(2026, 6, 1), D(2026, 6, 2)],
        start_time=datetime.time(5, 0),
        end_time=datetime.time(6, 0),
        hours=1.0,
        kind="extra",
        side="before",
        return_to="/week/1?year=2026&week=23",
        session=test_db,
        current_user=test_user,
    )
    location = response.headers["location"]
    assert "success=" in location
    assert "result=" not in location
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_multidate_edit_routes.py -q`
Expected: FAIL, the location still carries `result=`.

- [ ] **Step 3: Rename the parameter**

In `app/core/helpers.py`, in `result_param`, change the returned prefix from `"?result="` to `"?success="` and update its docstring to say which query parameter it produces and which template pattern renders it.

- [ ] **Step 4: Accept and render it**

In `app/routes/schedule_personal.py`, add `success: str | None = Query(None),` to `show_day_for_person`, `show_week_for_person` and `show_month_for_person`, and `"success": success,` to each of their context dicts. `Query` is already imported.

At the top of `{% block page_content %}` in `day.html`, `week.html` and `month.html`:

```jinja
    {% if success %}
    <div class="alert alert--success">{{ success }}</div>
    {% endif %}
```

- [ ] **Step 5: Run the tests**

Run: `venv/bin/python3 -m pytest tests/test_multidate_edit_routes.py tests/test_day_edit_panel.py -q`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
venv/bin/python3 -m ruff check . && venv/bin/python3 -m ruff format --check .
git add -A
git commit -m "$(cat <<'MSG'
fix(routes): report skipped dates through the success parameter

A multi-date post built a skip report into the redirect and no view rendered it,
so a conflicting day looked like it had silently done nothing. The report now
uses the success query parameter and the alert markup the admin pages already
use, and the day, week and month views render it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MgsRgKYs94sawwQ12fmxMC
MSG
)"
```

---

### Task 2: Cap the dates per post

**Files:**
- Modify: `app/core/helpers.py` (`apply_to_dates`)
- Test: `tests/test_multidate_edit_routes.py` (extend)

**Interfaces:**
- Produces: `apply_to_dates` raises `HTTPException(400)` when given more than `MAX_EDIT_DATES` dates. `MAX_EDIT_DATES = 62` (two months, the widest selection a month view can produce).

The spec's known ceiling: the loop writes one row per date with no batching. A cap is the guard, not batching logic. Put it in `apply_to_dates` so all four routes inherit it from one place, and mark it with a `ponytail:` comment naming the ceiling and the upgrade path.

- [ ] **Step 1: Write the failing test**

```python
def test_too_many_dates_is_rejected():
    """The write loop has no batching, so the cap is the guard."""
    from fastapi import HTTPException

    from app.core.helpers import MAX_EDIT_DATES

    dates = [D(2026, 1, 1) + datetime.timedelta(days=i) for i in range(MAX_EDIT_DATES + 1)]
    with pytest.raises(HTTPException) as exc:
        apply_to_dates(dates, lambda d: None, lambda d: None)
    assert exc.value.status_code == 400


def test_exactly_the_cap_is_allowed():
    dates = [D(2026, 1, 1) + datetime.timedelta(days=i) for i in range(MAX_EDIT_DATES)]
    written, skipped = apply_to_dates(dates, lambda d: None, lambda d: None)
    assert len(written) == MAX_EDIT_DATES
```

Add `from app.core.helpers import MAX_EDIT_DATES` to the file's imports.

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_multidate_edit_routes.py -q`
Expected: FAIL, `ImportError: cannot import name 'MAX_EDIT_DATES'`.

- [ ] **Step 3: Add the cap**

In `app/core/helpers.py`:

```python
# ponytail: one insert per date, no batching. 62 days is two months, the widest
# a month view can select. Batch the writes if a real use case needs more.
MAX_EDIT_DATES = 62
```

At the top of `apply_to_dates`:

```python
    if len(dates) > MAX_EDIT_DATES:
        raise HTTPException(status_code=400, detail=f"Too many dates, the limit is {MAX_EDIT_DATES}")
```

`HTTPException` is already imported in that file.

- [ ] **Step 4: Run and commit**

```bash
venv/bin/python3 -m pytest tests/test_multidate_edit_routes.py -q
venv/bin/python3 -m ruff check . && venv/bin/python3 -m ruff format --check .
git add -A
git commit -m "$(cat <<'MSG'
feat(routes): cap the number of dates one edit post may write

The write loop inserts one row per date with no batching, so the guard is a
limit rather than batching logic. It lives in apply_to_dates, which all four
edit routes already go through.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MgsRgKYs94sawwQ12fmxMC
MSG
)"
```

---

### Task 3: Selectable calendar cells

**Files:**
- Modify: `app/templates/month.html:160`, `app/templates/week.html:52`
- Create: `app/static/js/day-selection.js`
- Modify: `app/static/css/components.css`
- Modify: `app/core/translations.py`
- Test: `tests/test_multiday_selection.py`

**Interfaces:**
- Produces: every `td.calendar-day` carries `data-date="YYYY-MM-DD"` and `aria-pressed="false"`.
- Produces: `app/static/js/day-selection.js`, which toggles `.is-selected` on click, extends the range on shift-click, clears on Escape, and dispatches a `day-selection-change` CustomEvent whose `detail.dates` is the sorted list of selected `YYYY-MM-DD` strings. Task 4's drawer listens for that event, so the two halves stay independent.

Selection is the whole cell except the day-number link. Click the date to open the day page, click anywhere else to select.

- [ ] **Step 1: Write the failing test**

Create `tests/test_multiday_selection.py`. Copy the `env` fixture from `tests/test_partial_absence_views.py` verbatim, including the `PersonHistory` row and the `sessionmaker(bind=test_db.get_bind())` patch (a bare `lambda: test_db` detaches the User on views that open several sessions). Then define the module constants the tests below use:

```python
import datetime

import pytest

DAY = datetime.date(2026, 3, 2)
ISO_YEAR, ISO_WEEK, _ = DAY.isocalendar()

VIEWS = {
    "month": f"/month/1?year={DAY.year}&month={DAY.month}",
    "week": f"/week/1?year={ISO_YEAR}&week={ISO_WEEK}",
}
```

```python
@pytest.mark.parametrize("view", ["month", "week"])
def test_every_calendar_cell_carries_its_date(env, view):
    client, _ = env
    html = client.get(VIEWS[view]).text
    assert f'data-date="{DAY.isoformat()}"' in html


@pytest.mark.parametrize("view", ["month", "week"])
def test_the_selection_script_is_loaded(env, view):
    client, _ = env
    html = client.get(VIEWS[view]).text
    assert "day-selection.js" in html


@pytest.mark.parametrize("view", ["month", "week"])
def test_cells_are_marked_as_toggles_for_screen_readers(env, view):
    client, _ = env
    html = client.get(VIEWS[view]).text
    assert 'aria-pressed="false"' in html
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_multiday_selection.py -q`
Expected: three failures per view.

- [ ] **Step 3: Add the attributes**

`month.html`, on the `<td class="calendar-day ...">` at line 160, add:

```jinja
                            data-date="{{ day_data.date.isoformat() }}" aria-pressed="false"
```

`week.html`, on the `<td class="calendar-day ...">` at line 52, add the same with `day.date`.

- [ ] **Step 4: Write the script**

Create `app/static/js/day-selection.js`:

```javascript
// Select days in the month and week calendars.
//
// Both views render identical markup (table.calendar-grid > td.calendar-day),
// so one script drives both. The day-number link keeps working: a click on it
// navigates and never toggles.
//
// Emits `day-selection-change` on document with detail.dates, the sorted list of
// YYYY-MM-DD strings, so the edit drawer can stay independent of this file.
(function () {
    var cells = Array.prototype.slice.call(document.querySelectorAll('td.calendar-day[data-date]'));
    if (!cells.length) return;

    var lastIndex = null;

    function selected() {
        return cells.filter(function (c) { return c.classList.contains('is-selected'); })
                    .map(function (c) { return c.dataset.date; })
                    .sort();
    }

    function announce() {
        document.dispatchEvent(new CustomEvent('day-selection-change', {
            detail: { dates: selected() }
        }));
    }

    function setSelected(cell, on) {
        cell.classList.toggle('is-selected', on);
        cell.setAttribute('aria-pressed', on ? 'true' : 'false');
    }

    function clearAll() {
        cells.forEach(function (c) { setSelected(c, false); });
        lastIndex = null;
        announce();
    }

    cells.forEach(function (cell, index) {
        cell.addEventListener('click', function (event) {
            // The date link navigates; everything else in the cell selects.
            if (event.target.closest('a')) return;

            if (event.shiftKey && lastIndex !== null) {
                var from = Math.min(lastIndex, index);
                var to = Math.max(lastIndex, index);
                for (var i = from; i <= to; i++) setSelected(cells[i], true);
            } else {
                setSelected(cell, !cell.classList.contains('is-selected'));
                lastIndex = index;
            }
            announce();
        });
    });

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') clearAll();
    });

    document.addEventListener('day-selection-clear', clearAll);
})();
```

Load it from `app/templates/base.html`, on the line after `shift-colors.js` at line 23:

```jinja
        <script src="/static/js/day-selection.js?v={{ static_version }}" defer></script>
```

The variable is `static_version`, not `app_version`, and scripts live in `base.html`'s head rather than per template. Loading it globally is safe because the script returns immediately when the page has no `td.calendar-day[data-date]`, which is every page but the month and week views.

The test in Step 1 asserts the script reaches those two views; since it is loaded in the base layout it reaches every page, which is what we want and what the assertion checks.

- [ ] **Step 5: Add the CSS**

In `app/static/css/components.css`, beside the other `.calendar-day` rules:

```css
/* Multi-day selection. The cell is the hit area; the date link keeps navigating. */
td.calendar-day[data-date] { cursor: pointer; }
td.calendar-day.is-selected { outline: 2px solid var(--accent); outline-offset: -2px; }
td.calendar-day.is-selected .calendar-day-content { background: rgba(123, 211, 137, 0.10); }
```

Use `outline` rather than `border`: `.calendar-day` already has `data-border-top` painting its top border, and a border here would fight it.

- [ ] **Step 6: Run and commit**

```bash
venv/bin/python3 -m pytest tests/test_multiday_selection.py -q
venv/bin/python3 -m ruff check . && venv/bin/python3 -m ruff format --check .
git add -A
git commit -m "$(cat <<'MSG'
feat(calendar): select days in the month and week views

Cells carry their date and act as toggles: click to select, shift-click to
extend, Escape to clear. The day-number link still navigates, so the calendar
stays usable as a calendar.

One script serves both views because they render identical markup. It emits
day-selection-change rather than touching the edit drawer, so the two halves
stay independent.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MgsRgKYs94sawwQ12fmxMC
MSG
)"
```

---

### Task 4: The edit drawer

**Files:**
- Modify: `app/templates/month.html`, `app/templates/week.html`
- Modify: `app/routes/schedule_personal.py` (week and month routes)
- Modify: `app/core/translations.py`
- Test: `tests/test_multiday_selection.py` (extend)

**Interfaces:**
- Consumes: the `day-selection-change` event from Task 3.
- Produces: `_day_edit_panel.html` rendered once at the bottom of each view inside `<div id="day-edit-drawer" hidden>`, with the panel's own script filling the `dates` inputs from the current selection on submit.

The partial already renders against `edit_dates`, a list. The week and month routes pass an empty list, and the script writes the real dates in at submit time, so opening the drawer costs no server round trip.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("view", ["month", "week"])
def test_the_drawer_renders_hidden_with_the_edit_forms(env, view):
    client, _ = env
    html = client.get(VIEWS[view]).text
    assert 'id="day-edit-drawer"' in html
    for action in ("/overtime/add", "/absence/add", "/oncall/add", "/shift-override/add"):
        assert f'action="{action}"' in html


@pytest.mark.parametrize("view", ["month", "week"])
def test_the_drawer_forms_return_to_this_view(env, view):
    client, _ = env
    html = client.get(VIEWS[view]).text
    assert 'name="return_to"' in html
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_multiday_selection.py -q`
Expected: the two new tests fail per view.

- [ ] **Step 3: Give the routes what the partial needs**

The partial reads `edit_dates`, `person_id`, `date`, `csrf_token`, `shift`, `absence`, `oncall_override`, `shift_override`, `ot_rows`, `standard_shifts` and `swap_users`. The week and month routes pass almost none of those.

Render the drawer with the day-independent subset and an empty selection:

```python
            # The drawer renders the same partial as the day page, against an empty
            # selection. The selection script writes the real dates in on submit, so
            # opening it needs no round trip. The per-day context the partial reads
            # (absence, oncall_override, shift_override, ot_rows) is deliberately
            # absent: with many days selected there is no single day's state to show.
            "edit_dates": [],
            "drawer": True,
            "standard_shifts": [s for s in get_shift_types() if s.code in ("N1", "N2", "N3")],
```

In `_day_edit_panel.html`, guard every block that reads per-day state with `{% if not drawer %}`, so the drawer shows the add forms and not the "current absence" tables. Set `{% set drawer = drawer | default(false) %}` at the top beside the existing `edit_dates` default, so the day page is unaffected.

- [ ] **Step 4: Render the drawer**

At the bottom of `month.html` (before `{{ super() }}`) and of `week.html` (before `{% endblock %}`):

```jinja
    <div id="day-edit-drawer" hidden>
        <p class="day-selection-summary"><span id="day-selection-count"></span></p>
        {% include "_day_edit_panel.html" with context %}
    </div>
```

The forms need a `return_to` pointing back at this view. The partial already emits `return_to` as the day URL; in drawer mode it must be the current view instead. Add to the partial, replacing the existing hidden `return_to` inputs:

```jinja
                <input type="hidden" name="return_to" value="{{ return_to_url | default('/day/' ~ person_id ~ '/' ~ date.year ~ '/' ~ date.month ~ '/' ~ date.day) }}">
```

and pass `"return_to_url": str(request.url.path) + ("?" + str(request.url.query) if request.url.query else "")` from the week and month routes.

- [ ] **Step 5: Wire the drawer to the selection**

In a `<script>` block in both templates:

```javascript
(function () {
    var drawer = document.getElementById('day-edit-drawer');
    var count = document.getElementById('day-selection-count');
    if (!drawer) return;
    var current = [];

    document.addEventListener('day-selection-change', function (event) {
        current = event.detail.dates;
        drawer.hidden = current.length === 0;
        count.textContent = '{{ t.multiday_selected }}'.replace('{n}', current.length);
    });

    // The dates are written in at submit time, so selecting a day costs no round trip.
    drawer.addEventListener('submit', function (event) {
        var form = event.target;
        form.querySelectorAll('input[name="dates"]').forEach(function (el) { el.remove(); });
        current.forEach(function (d) {
            var input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'dates';
            input.value = d;
            form.appendChild(input);
        });
    });
})();
```

New keys, both languages: `multiday_selected` (`{n} dagar markerade` / `{n} days selected`).

- [ ] **Step 6: Run the tests, the full suite, and commit**

```bash
venv/bin/python3 -m pytest tests/test_multiday_selection.py tests/test_day_edit_panel.py -q
venv/bin/python3 -m pytest -q -p no:cacheprovider 2>&1 | tail -3
venv/bin/python3 -m ruff check . && venv/bin/python3 -m ruff format --check .
git add -A
git commit -m "$(cat <<'MSG'
feat(calendar): edit every selected day from one drawer

The month and week views render the day page's edit partial against the current
selection. The dates go in at submit time, so opening the drawer costs no round
trip, and the four routes need no change: they have taken a list of dates since
the edit panel branch.

Per-day state (the current absence, on-call override and overtime rows) is
hidden in drawer mode, because with many days selected there is no single day's
state to show.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MgsRgKYs94sawwQ12fmxMC
MSG
)"
```

---

### Task 5: Verify in a browser

**Files:**
- Modify: none.

Use the `browse` skill. It needs `GSTACK_CHROMIUM_NO_SANDBOX=1` on the call that starts the daemon, or Chromium fails to launch on this machine.

- [ ] **Step 1: Select and post**

Log in to `http://127.0.0.1:8001` as `ddf412` (the password is given in-session; do not write it into any file). Open `/month/6?year=2026&month=10`, click three days, confirm the drawer appears and names three days, add extra time from the Time tab, and confirm three rows were written:

```bash
sqlite3 app/database/schedule.db "SELECT date, kind, side FROM overtime_shifts WHERE user_id=6 AND date LIKE '2026-10%';"
```

- [ ] **Step 2: Confirm the skip report renders**

Post the same segment over the same three days again. Expected: the redirect lands back on the month view and the green alert names three skipped days. This is the check that Task 1 actually connected.

- [ ] **Step 3: Confirm the date link still navigates**

Click a day number. Expected: the day page opens and the day was not toggled.

- [ ] **Step 4: Check 400 px**

`viewport 400x800`, reload, select two days, confirm the drawer is reachable, the tab row wraps or fits, and `document.documentElement.scrollWidth` equals `window.innerWidth`.

- [ ] **Step 5: Clean up and report**

Delete the rows written in Step 1, restart the container, and report: the full suite count, the traceback count from `docker logs periodical_dev --since 10m 2>&1 | grep -c Traceback`, and what the skip alert said.

---

## What this branch does not do

No selection in `month_all`, `week_all` or `year_all`. The user ruled that out explicitly on 2026-09-16: the all-person overview views do not need it.

No batching of the writes. The cap in Task 2 is the deliberate ceiling.
