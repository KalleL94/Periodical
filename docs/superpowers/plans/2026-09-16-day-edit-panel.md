# Day Edit Panel Implementation Plan (branch 1b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make branch 1a's capabilities reachable from a browser, and rebuild the day page's edit area so three more forms do not make it unusable.

**Architecture:** The four edit routes stop taking one `date` and start taking `dates: list[date_cls]`, plus a validated `return_to`. A multi-date post skips conflicting dates and reports them; a single-date post keeps today's upsert. The edit panel moves out of `day.html` into `app/templates/_day_edit_panel.html` with four tabs, and the Tid tab lists the day's segments and offers one form whose type and side selects decide what is added. The partial takes a list of dates so branch 2 can render it against a calendar selection without a rewrite.

**Tech Stack:** FastAPI, Jinja2, vanilla JS, CSP-safe CSS class toggles. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-12-day-time-segments-design.md`

**Depends on:** branch `feat/day-segment-types`. `OvertimeShift.kind`/`side`, the `ETC` override and `get_overtime_rows_for_date` must exist before Task 3.

## Global Constraints

- **Branch from `feat/day-segment-types`, not `main`.** Both parents are unmerged.
- **The partial takes a list of dates, never a scalar.** The day page passes one. Building it around a single date means rewriting it in branch 2, which is the whole reason the partial exists.
- **Conflict skipping applies only when two or more dates are posted.** With one date every route behaves exactly as it does today: an upsert that replaces what is there. `/absence/add` already works that way and the day page depends on it, because swapping VAB for SICK is that gesture.
- **`return_to` must be validated before use or it is an open redirect.** `is_safe_redirect` already exists in `app/routes/auth_routes.py:37`; Task 1 moves it to `app/core/helpers.py` so the edit routes can use it without importing from a route module.
- **Every new POST form needs the hidden `csrf_token` field** or it returns 403. `conftest`'s `CSRFTestClient` injects it for route tests.
- **No inline styles and no inline event handlers beyond the existing pattern.** Tab switching is a CSS class toggle on a container, matching `#pay-section.is-editing` in `day.html`.
- **Every user-facing string goes in `app/core/translations.py`, in both the Swedish and the English block.** The file is one dict per language; a key present in one and missing from the other renders as a blank.
- **All source code comments in English. No em dash (—) anywhere.**
- **Run from the repo root** using `venv/bin/python3`.

---

### Task 1: The routes take a list of dates

**Files:**
- Modify: `app/core/helpers.py` (add `is_safe_redirect`)
- Modify: `app/routes/auth_routes.py:37-43` (import it from helpers instead)
- Modify: `app/routes/overtime.py`, `app/routes/profile.py` (`add_absence`), `app/routes/oncall.py` (`add_oncall_override`), `app/routes/shift_override.py` (`add_shift_override`)
- Test: `tests/test_multidate_edit_routes.py`

**Interfaces:**
- Produces: each of the four add-routes accepts `dates: list[date_cls] = Form(...)` and `return_to: str = Form("")`, and returns a redirect to `return_to` when it is a safe relative path, otherwise to the day page of the first date. A multi-date post appends `?result=<encoded>` naming what was written and what was skipped.
- Produces: `is_safe_redirect(url: str) -> bool` in `app/core/helpers.py`, moved verbatim.
- Produces: `apply_to_dates(dates, write, conflicts) -> tuple[list, list]` in `app/core/helpers.py`, the shared loop. `write(date)` performs one date's write. `conflicts(date)` returns a reason string or None. With one date, `conflicts` is not consulted at all.

- [ ] **Step 1: Write the failing test**

Create `tests/test_multidate_edit_routes.py`. Use the same client and fixtures as `tests/test_overtime_upsert.py`, which already calls the route functions directly with a test session.

```python
"""Posting several dates to one edit route writes one row per date.

The conflict rule is deliberately asymmetric: one date upserts, several skip.
"""

import datetime
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# ruff: noqa: E402
from app.core.helpers import apply_to_dates, is_safe_redirect

D = datetime.date


def test_is_safe_redirect_rejects_absolute_and_protocol_relative():
    assert is_safe_redirect("/day/6/2026/9/17") is True
    assert is_safe_redirect("https://evil.example/x") is False
    assert is_safe_redirect("//evil.example/x") is False
    assert is_safe_redirect("") is False


def test_one_date_never_consults_the_conflict_check():
    """A single-date post upserts, which is what the day page relies on."""
    seen = []
    done = []

    def conflicts(date):
        seen.append(date)
        return "should not be asked"

    written, skipped = apply_to_dates([D(2026, 6, 1)], done.append, conflicts)
    assert seen == []
    assert skipped == []
    assert written == [D(2026, 6, 1)]
    assert done == [D(2026, 6, 1)]


def test_several_dates_skip_the_conflicting_one_and_write_the_rest():
    done = []
    dates = [D(2026, 6, 1), D(2026, 6, 2), D(2026, 6, 3)]

    def conflicts(date):
        return "hade redan VAB" if date == D(2026, 6, 2) else None

    written, skipped = apply_to_dates(dates, done.append, conflicts)
    assert written == [D(2026, 6, 1), D(2026, 6, 3)]
    assert skipped == [(D(2026, 6, 2), "hade redan VAB")]
    assert done == [D(2026, 6, 1), D(2026, 6, 3)]
```

Then a route-level test in the same file, following `tests/test_overtime_upsert.py`'s `test_db` / `test_user` fixtures:

```python
@pytest.mark.anyio
async def test_posting_four_dates_creates_four_overtime_rows(test_db, test_user):
    from app.database.database import OvertimeShift
    from app.routes.overtime import add_overtime_shift

    dates = [D(2026, 6, 1), D(2026, 6, 2), D(2026, 6, 3), D(2026, 6, 4)]
    await add_overtime_shift(
        user_id=test_user.id,
        dates=dates,
        start_time=datetime.time(5, 0),
        end_time=datetime.time(6, 0),
        hours=1.0,
        kind="extra",
        side="before",
        return_to="/week/1?year=2026&week=23",
        session=test_db,
        current_user=test_user,
    )
    rows = test_db.query(OvertimeShift).all()
    assert {r.date for r in rows} == set(dates)


@pytest.mark.anyio
async def test_return_to_is_honoured_when_relative(test_db, test_user):
    from app.routes.overtime import add_overtime_shift

    response = await add_overtime_shift(
        user_id=test_user.id,
        dates=[D(2026, 6, 1)],
        start_time=datetime.time(5, 0),
        end_time=datetime.time(6, 0),
        hours=1.0,
        kind="extra",
        side="before",
        return_to="/week/1?year=2026&week=23",
        session=test_db,
        current_user=test_user,
    )
    assert response.headers["location"].startswith("/week/1")


@pytest.mark.anyio
async def test_an_absolute_return_to_falls_back_to_the_day_page(test_db, test_user):
    from app.routes.overtime import add_overtime_shift

    response = await add_overtime_shift(
        user_id=test_user.id,
        dates=[D(2026, 6, 1)],
        start_time=datetime.time(5, 0),
        end_time=datetime.time(6, 0),
        hours=1.0,
        kind="extra",
        side="before",
        return_to="https://evil.example/steal",
        session=test_db,
        current_user=test_user,
    )
    assert response.headers["location"] == "/day/1/2026/6/1"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_multidate_edit_routes.py -q`
Expected: collection error, `ImportError: cannot import name 'apply_to_dates'`

- [ ] **Step 3: Add the two helpers**

In `app/core/helpers.py`:

```python
def is_safe_redirect(url: str) -> bool:
    """True when url is a local path, so it cannot become an open redirect."""
    if not url:
        return False
    parsed = urlparse(url)
    return not parsed.scheme and not parsed.netloc and url.startswith("/") and not url.startswith("//")


def apply_to_dates(dates, write, conflicts):
    """Run write(date) for each date, skipping conflicts when more than one is given.

    A single-date post is a deliberate edit of one day: the caller can see what it
    replaces, so it upserts and the conflict check is never consulted. Ten dates at
    once is a different act, and silently overwriting nine days is the kind of error
    that surfaces a month later in a pay forecast.

    Returns (written dates, [(skipped date, reason)]).
    """
    written, skipped = [], []
    for date in dates:
        reason = conflicts(date) if len(dates) > 1 else None
        if reason:
            skipped.append((date, reason))
            continue
        write(date)
        written.append(date)
    return written, skipped
```

Add `from urllib.parse import urlparse` to that file's imports. In `app/routes/auth_routes.py`, delete the local `is_safe_redirect` and import it from `app.core.helpers`; its two call sites are unchanged.

- [ ] **Step 4: Change the four routes**

In each of `add_overtime_shift`, `add_absence`, `add_oncall_override` and `add_shift_override`:

- replace the `date` parameter with `dates: list[date_cls] = Form(...)` and add `return_to: str = Form("")`;
- move the per-date write into a local `def write(date):` closure and the conflict test into `def conflicts(date):`;
- call `apply_to_dates`, then `session.commit()` and `clear_schedule_cache()` **once**, after the loop;
- redirect to `return_to` when `is_safe_redirect(return_to)`, else to the first date's day page.

`add_absence` takes its date as `date_str: str = Form(..., alias="date")` and parses it by hand; replace that with `dates: list[date_cls] = Form(...)` and delete the `strptime` block, since FastAPI validates each entry.

The conflict test per route, from the spec:

| Route | `conflicts(date)` returns a reason when the date has |
|---|---|
| `/overtime/add` | a row with the same `(kind, side)`, or falls on a vacation day |
| `/absence/add` | an existing absence |
| `/oncall/add` | an existing on-call override |
| `/shift-override/add` | an absence, or week-based vacation |

Build the redirect result as a query string, so the receiving page can render it:

```python
def _result_param(written: list, skipped: list) -> str:
    """The ?result= fragment describing a multi-date write. Empty for one date."""
    if not skipped and len(written) <= 1:
        return ""
    parts = [f"{len(written)} dagar satta"]
    if skipped:
        detail = ", ".join(f"{d.strftime('%-d %b')} {reason}" for d, reason in skipped)
        parts.append(f"{len(skipped)} hoppades över: {detail}")
    return "?result=" + quote(". ".join(parts))
```

Put `_result_param` in `app/core/helpers.py` beside `apply_to_dates`, and import `quote` from `urllib.parse`.

- [ ] **Step 5: Run the tests**

Run: `venv/bin/python3 -m pytest tests/test_multidate_edit_routes.py tests/test_overtime_upsert.py -q`
Expected: all passed.

- [ ] **Step 6: Update every existing caller of the four routes**

Exactly **five** hidden inputs in `day.html` change to `name="dates"`, and three other matches must be left alone. A blanket search-and-replace on `name="date"` breaks all three with a 422, so work from this table, not from grep:

| `day.html` line | Its form posts to | Change? |
|---|---|---|
| 212 | `/overtime/add` | yes |
| 260 | `/overtime/add` | yes |
| 465 | `/absence/add` | yes |
| 562 | `/oncall/remove` | **no**, not one of the four |
| 583 | `/oncall/add` | yes |
| 609 | `/oncall/add` | yes |
| 666 | `/shift-override/add` | yes |
| 980 | `/day-pay-override/set` | **no**, a different route |

`admin_substitute_manage.html` lines 109 and 148 post to `/admin/substitutes/{id}/absence/add` and `/admin/substitutes/{id}/shift/add`, which are separate routes in `substitutes.py` that keep their scalar `date`. Leave them.

`/oncall/remove` is a sibling that would also make sense across dates (cancelling a week of standby), but the spec scopes this branch to the four add-routes. Leaving it scalar is deliberate, not an oversight.

Line numbers shift as you edit, so verify each one by the `action=` of its enclosing `<form>` rather than trusting the number.

Run: `venv/bin/python3 -m pytest -q 2>&1 | tail -3`
Expected: the same pass count as before this task, plus the new tests.

- [ ] **Step 7: Lint and commit**

```bash
venv/bin/python3 -m ruff check . && venv/bin/python3 -m ruff format --check .
git add -A
git commit -m "$(cat <<'MSG'
feat(routes): let the four day-edit routes take several dates

Each add-route now takes dates as a list and an optional return_to. One date
behaves exactly as before, an upsert, because the day page depends on that when
swapping one absence type for another. Two or more skip conflicting dates and
name them in the redirect, since silently overwriting nine days is the kind of
error that only surfaces in a pay forecast a month later.

return_to is validated with is_safe_redirect, moved from auth_routes to
core.helpers so the edit routes need not import from a route module.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MgsRgKYs94sawwQ12fmxMC
MSG
)"
```

---

### Task 2: Extract the panel into a partial, unchanged

**Files:**
- Create: `app/templates/_day_edit_panel.html`
- Modify: `app/templates/day.html:125-694`
- Test: `tests/test_day_edit_panel.py`

**Interfaces:**
- Produces: a partial included as `{% include "_day_edit_panel.html" with context %}`, reading a new context variable `edit_dates` (a list of `date` objects) instead of the page's scalar `date`.
- Produces: `app/routes/schedule_personal.py` passes `"edit_dates": [date_obj]` alongside the existing `"date"`.

This task moves markup and changes nothing a user can see. Do it before Task 3 so the tab work lands in a file that is already the right shape.

- [ ] **Step 1: Write the characterization test first**

```python
"""The edit panel renders the same forms after the move into a partial."""


def test_the_day_page_still_offers_every_edit_form(client, logged_in_user):
    html = client.get("/day/1/2026/6/1").text
    for action in ("/overtime/add", "/absence/add", "/oncall/add", "/shift-override/add"):
        assert f'action="{action}"' in html


def test_every_form_carries_a_csrf_token(client, logged_in_user):
    """A POST form without it returns 403, so a missing one is an invisible break."""
    import re

    html = client.get("/day/1/2026/6/1").text
    forms = re.findall(r"<form[^>]*method=\"POST\".*?</form>", html, re.S)
    assert forms
    assert all("csrf_token" in f for f in forms)


def test_the_panel_posts_dates_not_date(client, logged_in_user):
    html = client.get("/day/1/2026/6/1").text
    assert 'name="dates"' in html
    assert 'name="date"' not in html
```

Use the fixture style of `tests/test_day_view_consistency.py`, which already drives the day route through a logged-in client.

- [ ] **Step 2: Run it**

Run: `venv/bin/python3 -m pytest tests/test_day_edit_panel.py -q`
Expected: the first two pass against the current page, the third fails until Task 1's template change is in. If the first two fail, the fixtures are wrong; fix them before moving markup, or the move has no safety net.

- [ ] **Step 3: Move the markup**

Cut `day.html` from the line after `<div id="day-edit-panel"...>` to the matching `</div>{# /day-edit-panel #}` and paste it into `app/templates/_day_edit_panel.html`. Leave the `<button id="day-edit-btn">` and the wrapping div in `day.html`. Replace the cut region with:

```jinja
            {% include "_day_edit_panel.html" with context %}
```

At the top of the partial, add:

```jinja
{# app/templates/_day_edit_panel.html
   The day page's edit forms. Rendered against edit_dates, a list, so the same
   partial serves the day page (one date) and branch 2's calendar selection
   (many). Every form posts its dates as repeated hidden inputs. #}
{% set edit_dates = edit_dates | default([date]) %}
```

Replace every `<input type="hidden" name="date" value="{{ date }}">` in the partial with:

```jinja
                        {% for d in edit_dates %}<input type="hidden" name="dates" value="{{ d }}">{% endfor %}
```

- [ ] **Step 4: Pass `edit_dates` from the route**

In `app/routes/schedule_personal.py`, in the day route's context dict beside `"date"`, add:

```python
            # The edit panel is a list-of-dates partial so branch 2 can reuse it.
            "edit_dates": [date_obj],
```

- [ ] **Step 5: Run the tests**

Run: `venv/bin/python3 -m pytest tests/test_day_edit_panel.py tests/test_day_view_consistency.py -q`
Expected: all passed.

- [ ] **Step 6: Confirm the page is byte-identical apart from the dates rename**

Capture the page before and after with the script described in Task 5, and diff. The only expected differences are `name="date"` becoming `name="dates"`. Anything else means markup was lost in the move.

- [ ] **Step 7: Lint and commit**

```bash
venv/bin/python3 -m ruff check . && venv/bin/python3 -m ruff format --check .
git add -A
git commit -m "$(cat <<'MSG'
refactor(day): move the edit panel into a partial

day.html was 1062 lines with four stacked forms. The panel now lives in
_day_edit_panel.html and renders against edit_dates, a list, so branch 2 can
render the same partial against a calendar selection instead of rewriting it.

No visible change: the only markup difference is the hidden date field becoming
a repeated dates field.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MgsRgKYs94sawwQ12fmxMC
MSG
)"
```

---

### Task 3: Tabs, and the Tid tab's segment list and unified form

**Files:**
- Modify: `app/templates/_day_edit_panel.html`
- Modify: `app/routes/schedule_personal.py` (pass `ot_rows`)
- Modify: `app/core/translations.py` (new keys, both languages)
- Test: `tests/test_day_edit_panel.py` (extend)

**Interfaces:**
- Consumes: `get_overtime_rows_for_date` from branch 1a.
- Produces: context key `ot_rows`, the day's `OvertimeShift` rows ordered by id, each carrying `kind`, `side`, `start_time`, `end_time`, `hours`, `id`.
- Produces: four tabs whose panels are shown by a CSS class on `#day-edit-panel`, set by one click handler.

The day route currently passes `ot_shift` (a details dict) and `ot_shift_id` (one id). Both stay, because the pay section reads them. `ot_rows` is added beside them for the list.

- [ ] **Step 1: Write the failing test**

```python
def test_the_tid_tab_lists_every_overtime_row(client, logged_in_user, session):
    """Two rows on one day must both appear, each with its own delete button."""
    import datetime

    from app.database.database import OvertimeShift

    for side, start, end in (("before", "05:00", "06:00"), ("after", "14:30", "16:30")):
        session.add(
            OvertimeShift(
                user_id=1,
                date=datetime.date(2026, 6, 1),
                start_time=datetime.time.fromisoformat(start),
                end_time=datetime.time.fromisoformat(end),
                hours=1.0,
                ot_pay=0.0,
                kind="ot",
                side=side,
            )
        )
    session.commit()

    html = client.get("/day/1/2026/6/1").text
    rows = [r for r in session.query(OvertimeShift).all()]
    for row in rows:
        assert f"/overtime/{row.id}/delete" in html


def test_the_add_form_offers_kind_and_side(client, logged_in_user):
    html = client.get("/day/1/2026/6/1").text
    assert 'name="kind"' in html
    assert 'name="side"' in html


def test_all_four_tabs_render(client, logged_in_user):
    html = client.get("/day/1/2026/6/1").text
    for tab in ("tab-tid", "tab-franvaro", "tab-beredskap", "tab-byte"):
        assert tab in html
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_day_edit_panel.py -q`
Expected: the three new tests fail; the Task 2 tests still pass.

- [ ] **Step 3: Pass the rows from the route**

In `app/routes/schedule_personal.py`, beside the existing `_ot_row` lookup at line 310:

```python
    # The Tid tab lists every row; ot_shift_id above stays for the pay section's
    # delete link, which still speaks about a single primary row.
    ot_rows = get_overtime_rows_for_date(db, user_id_for_wages, date_obj)
```

Add `"ot_rows": ot_rows,` to the context dict and `get_overtime_rows_for_date` to the imports at line 35.

- [ ] **Step 4: Add the tab shell**

At the top of `_day_edit_panel.html`, inside the panel:

```jinja
    <div class="day-tabs" role="tablist">
        <button type="button" class="day-tab is-active" data-tab="tid" role="tab">{{ t.day_tab_time }}</button>
        <button type="button" class="day-tab" data-tab="franvaro" role="tab">{{ t.day_tab_absence }}</button>
        <button type="button" class="day-tab" data-tab="beredskap" role="tab">{{ t.day_tab_oncall }}</button>
        <button type="button" class="day-tab" data-tab="byte" role="tab">{{ t.day_tab_swap }}</button>
    </div>
```

Wrap each existing section of the partial in its own panel div:

| Panel id | Holds the section currently headed |
|---|---|
| `tab-tid` | `t.day_overtime` |
| `tab-franvaro` | `t.day_absence_title`, plus the salary-deduction tables under it |
| `tab-beredskap` | `t.day_oncall_title` |
| `tab-byte` | `t.day_shift_override_title`, plus the propose-swap form under `t.day_propose_swap` |

Each opens as `<div class="day-tab-panel" id="tab-tid">` and closes before the next begins. The `{% if %}` guards already wrapping those sections stay inside their panel, so a panel can render empty; that is fine, the tab still switches to it.

The CSS, in the `<style>` block at the bottom of `day.html`:

```css
.day-tabs { display: flex; gap: 0.25rem; flex-wrap: wrap; margin-bottom: 1rem; border-bottom: 1px solid var(--border); }
.day-tab { background: none; border: none; border-bottom: 2px solid transparent; padding: 0.5rem 0.9rem; color: var(--muted); cursor: pointer; font: inherit; }
.day-tab.is-active { color: var(--fg); border-bottom-color: var(--accent); }
.day-tab-panel { display: none; }
.day-tab-panel.is-active { display: block; }
```

The handler, in the `<script>` block at the bottom of `day.html`, beside `toggleDayEdit`:

```javascript
document.querySelectorAll('.day-tab').forEach(function (btn) {
    btn.addEventListener('click', function () {
        var name = btn.dataset.tab;
        document.querySelectorAll('.day-tab').forEach(function (b) {
            b.classList.toggle('is-active', b === btn);
        });
        document.querySelectorAll('.day-tab-panel').forEach(function (p) {
            p.classList.toggle('is-active', p.id === 'tab-' + name);
        });
    });
});
```

Give `#tab-tid` the `is-active` class in the markup so one panel shows before any click.

- [ ] **Step 5: Build the segment list and the unified add form**

Replace the Tid tab's two overtime forms with one list plus one form:

```jinja
    {% if ot_rows %}
    <table class="keep-table day-table-mt">
        <thead>
            <tr>
                <th>{{ t.day_seg_type }}</th>
                <th>{{ t.day_seg_when }}</th>
                <th class="th_right">{{ t.day_ext_hours }}</th>
                <th></th>
            </tr>
        </thead>
        <tbody>
        {% for row in ot_rows %}
            <tr>
                <td>{{ t.day_seg_kind_ot if row.kind == 'ot' else t.day_seg_kind_extra }}</td>
                <td class="day-nowrap">
                    {{ t.day_seg_side_full if row.side == 'full' else (t.day_seg_side_before if row.side == 'before' else t.day_seg_side_after) }}
                    {{ row.start_time.strftime('%H:%M') }}–{{ row.end_time.strftime('%H:%M') }}
                </td>
                <td class="td_right num">{{ "%.2f"|format(row.hours) }}</td>
                <td>
                    <form method="POST" action="/overtime/{{ row.id }}/delete" class="inline-form">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                        <button type="submit" class="btn btn-danger">{{ t.day_delete }}</button>
                    </form>
                </td>
            </tr>
        {% endfor %}
        </tbody>
    </table>
    {% endif %}

    <form method="POST" action="/overtime/add" class="ot-form">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <input type="hidden" name="user_id" value="{{ person_id }}">
        {% for d in edit_dates %}<input type="hidden" name="dates" value="{{ d }}">{% endfor %}
        <input type="hidden" name="return_to" value="/day/{{ person_id }}/{{ date.year }}/{{ date.month }}/{{ date.day }}">
        <div class="form-row">
            <div class="form-group">
                <label for="seg_kind">{{ t.day_seg_type }}</label>
                <select id="seg_kind" name="kind">
                    <option value="ot">{{ t.day_seg_kind_ot }}</option>
                    <option value="extra">{{ t.day_seg_kind_extra }}</option>
                </select>
            </div>
            <div class="form-group">
                <label for="seg_side">{{ t.day_seg_when }}</label>
                <select id="seg_side" name="side">
                    <option value="after">{{ t.day_seg_side_after }}</option>
                    <option value="before">{{ t.day_seg_side_before }}</option>
                    <option value="full">{{ t.day_seg_side_full }}</option>
                </select>
            </div>
            <div class="form-group">
                <label for="seg_start">{{ t.day_start_time }}</label>
                <input type="time" id="seg_start" name="start_time" required>
            </div>
            <div class="form-group">
                <label for="seg_end">{{ t.day_end_time }}</label>
                <input type="time" id="seg_end" name="end_time" required>
            </div>
            <div class="form-group">
                <label for="seg_hours">{{ t.day_ext_hours }}</label>
                <input type="number" id="seg_hours" name="hours" step="0.01" min="0.01" max="24" required>
            </div>
        </div>
        <button type="submit" class="btn btn-primary">{{ t.day_seg_add }}</button>
    </form>
    <p class="info-text">{{ t.day_seg_note }}</p>
```

Keep the existing hours-from-times script, retargeted at `seg_start`/`seg_end`/`seg_hours`, so the hours field fills itself and still crosses midnight on a negative difference.

- [ ] **Step 6: Add the translation keys**

Add to both the Swedish and the English block of `app/core/translations.py`:

| Key | Swedish | English |
|---|---|---|
| `day_tab_time` | `Tid` | `Time` |
| `day_tab_absence` | `Frånvaro` | `Absence` |
| `day_tab_oncall` | `Beredskap` | `On-call` |
| `day_tab_swap` | `Pass` | `Shift` |
| `day_seg_type` | `Typ` | `Type` |
| `day_seg_when` | `När` | `When` |
| `day_seg_kind_ot` | `Övertid` | `Overtime` |
| `day_seg_kind_extra` | `Mertid` | `Extra time` |
| `day_seg_side_before` | `Före passet` | `Before the shift` |
| `day_seg_side_after` | `Efter passet` | `After the shift` |
| `day_seg_side_full` | `Inkallad` | `Called in` |
| `day_seg_add` | `Lägg till tid` | `Add time` |
| `day_seg_note` | `Övertid betalas per ÖT-sats. Mertid ger OB men ingen extra grundlön på månadslön.` | `Overtime pays the OT rate. Extra time earns OB but no extra base pay on a monthly salary.` |

- [ ] **Step 7: Run the tests and commit**

```bash
venv/bin/python3 -m pytest tests/test_day_edit_panel.py tests/test_day_view_consistency.py -q
venv/bin/python3 -m pytest -q 2>&1 | tail -3
venv/bin/python3 -m ruff check . && venv/bin/python3 -m ruff format --check .
git add -A
git commit -m "$(cat <<'MSG'
feat(day): tabbed edit panel with a segment list and one add form

Four tabs replace four stacked forms, and the Time tab lists every overtime and
extra-time row on the day with its own delete button instead of assuming one.
One form with a type and a side select replaces the two separate overtime forms.

Tab switching is a CSS class toggle, the same CSP-safe pattern the pay section
already uses, so no inline styles and no new dependency.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MgsRgKYs94sawwQ12fmxMC
MSG
)"
```

---

### Task 4: The custom shift form

**Files:**
- Modify: `app/templates/_day_edit_panel.html` (the Pass tab)
- Modify: `app/core/translations.py`
- Test: `tests/test_day_edit_panel.py` (extend)

**Interfaces:**
- Consumes: `/shift-override/add` accepting `ETC` with `start_time`, `end_time` and `label`, from branch 1a Task 4.

- [ ] **Step 1: Write the failing test**

```python
def test_the_shift_tab_offers_the_custom_block(client, logged_in_user):
    html = client.get("/day/1/2026/6/1").text
    assert 'value="ETC"' in html
    assert 'name="label"' in html
```

- [ ] **Step 2: Run it**

Run: `venv/bin/python3 -m pytest tests/test_day_edit_panel.py -q`
Expected: the new test fails.

- [ ] **Step 3: Extend the shift-override form**

In the Pass tab's existing `/shift-override/add` form, add `ETC` to the select and three fields that only matter for it:

```jinja
                                <option value="ETC"{% if shift_override and shift_override.shift_code == 'ETC' %} selected{% endif %}>{{ t.day_shift_etc }}</option>
```

```jinja
        <div class="form-row" id="etc-fields">
            <div class="form-group">
                <label for="etc_label">{{ t.day_shift_etc_label }}</label>
                <input type="text" id="etc_label" name="label" maxlength="40"
                       value="{{ shift_override.label or '' if shift_override else '' }}">
            </div>
            <div class="form-group">
                <label for="etc_start">{{ t.day_start_time }}</label>
                <input type="time" id="etc_start" name="start_time"
                       value="{{ shift_override.start_time.strftime('%H:%M') if shift_override and shift_override.start_time else '' }}">
            </div>
            <div class="form-group">
                <label for="etc_end">{{ t.day_end_time }}</label>
                <input type="time" id="etc_end" name="end_time"
                       value="{{ shift_override.end_time.strftime('%H:%M') if shift_override and shift_override.end_time else '' }}">
            </div>
        </div>
```

Hide the three fields unless `ETC` is selected, with the same class-toggle pattern: give `#etc-fields` `hidden` by default and a change handler on the select that sets `document.getElementById('etc-fields').hidden = this.value !== 'ETC';`. The route rejects an `ETC` post without both times with a 400, so the fields being visible is a convenience, not the validation.

New keys, both languages: `day_shift_etc` (`ETC, Övrigt` / `ETC, Other`), `day_shift_etc_label` (`Etikett` / `Label`).

- [ ] **Step 4: Run and commit**

```bash
venv/bin/python3 -m pytest tests/test_day_edit_panel.py -q
venv/bin/python3 -m pytest -q 2>&1 | tail -3
venv/bin/python3 -m ruff check . && venv/bin/python3 -m ruff format --check .
git add -A
git commit -m "$(cat <<'MSG'
feat(day): add the custom shift block to the shift tab

ETC with a label and its own clock times is now reachable from the day page.
The three extra fields appear only when ETC is selected; the route is what
enforces that both times are present.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MgsRgKYs94sawwQ12fmxMC
MSG
)"
```

---

### Task 5: Verify in a browser

**Files:**
- Modify: none.

- [ ] **Step 1: Capture the day page before and after**

The dev container watches `app/` and serves :8001. Log in and dump the page the way branch 0's verification did:

```bash
SCRATCH=/tmp/claude-1000/-home-kakan-workspace-Periodical-dev/526e5643-be84-4348-a705-99a881369283/scratchpad
JAR=$(mktemp); BASE=http://127.0.0.1:8001
TOK=$(curl -s -c "$JAR" "$BASE/login" | grep -oP 'name="csrf_token" value="\K[^"]+' | head -1)
curl -s -b "$JAR" -c "$JAR" -o /dev/null -X POST "$BASE/login" \
  --data-urlencode "csrf_token=$TOK" --data-urlencode "username=ddf412" --data-urlencode "password=$DEV_PASSWORD"
curl -sL -b "$JAR" -o "$SCRATCH/panel.html" "$BASE/day/6/2026/9/17"
grep -c 'name="dates"' "$SCRATCH/panel.html"
```

Expected: at least four, one per form.

- [ ] **Step 2: Add extra time through the form, not the database**

Post the Tid tab's form with `kind=extra`, `side=before`, `05:00` to `06:00`, one hour, against an N1 day. Then reload the day page and confirm the hours rose by one and an OB code grew. This is the first time the feature is reachable without SQL, so it is the step that proves branch 1b did its job.

- [ ] **Step 3: Confirm no traceback**

```bash
docker logs periodical_dev --since 5m 2>&1 | grep -c Traceback
```

Expected: `0`.

- [ ] **Step 4: Check the page at phone width**

The tab row uses `flex-wrap`, so it must wrap rather than scroll. Confirm with the browser's device emulation at 400 px that the tabs wrap and no panel forces a horizontal scrollbar.

- [ ] **Step 5: Report**

State the full suite count, the traceback count, and what the hours and OB read before and after Step 2.

---

## What this branch does not do

No calendar selection. `edit_dates` is always a one-element list, because only the day page renders the partial. Branch 2 adds `data-date` to the month and week cells, a selection script, and a drawer that renders this same partial against many dates. Nothing in branch 2 should need to change a route or the partial's contract.
