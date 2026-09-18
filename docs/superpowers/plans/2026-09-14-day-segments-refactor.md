# Day Segments Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a day's worked time inside `period.py` from four rebound scalars into a list of intervals, with byte-identical output.

**Architecture:** `_resolve_effective_shift` currently returns `(shift, rotation_week, hours, start, end, ob)` and each overlay (`_apply_oncall_override`, `_apply_ot_display_shift`) rebinds those scalars in turn, so a day can hold exactly one worked interval. A new module `app/core/schedule/segments.py` introduces `DaySegment` plus three pure derive functions. `_ShiftResolution` carries `segments` instead of `hours`/`start`/`end`, the overlays operate on the list, and the two day builders derive the scalars back out immediately before writing the day dict. No key in the day dict changes, so no consumer outside `period.py` is touched.

**Tech Stack:** Python 3.14, SQLAlchemy, pytest, ruff. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-12-day-time-segments-design.md`

## Global Constraints

- **This branch changes no behaviour.** Output must be identical, field for field.
- **No test file may be edited.** The four characterization files are the grader. If one needs a change to pass, the refactor is wrong. Task 5 verifies this with `git diff`.
- **No day dict key changes.** `shift`, `original_shift`, `rotation_week`, `hours`, `start`, `end`, `ob`, `oncall_pay`, `oncall_details`, `ot_pay`, `ot_hours`, `ot_details`, `ob_hours_override`.
- **No migration, no new column, no route change.** Those belong to branch 1.
- **All source code comments in English.** Existing Swedish comments in `period.py` stay as they are; do not translate them, and do not add new Swedish ones.
- **No em dash (—) anywhere,** including code comments and commit messages. Use a comma, colon, parentheses or a full stop.
- **Commit messages carry no AI attribution** beyond the trailer block shown in each commit step.
- **Run from the repo root** using `venv/bin/python3`.

---

### Task 1: The segments module

**Files:**
- Create: `app/core/schedule/segments.py`
- Test: `tests/test_day_segments.py`

**Interfaces:**
- Consumes: `calculate_ob_hours(start_dt, end_dt, rules) -> dict[str, float]` from `app.core.schedule.ob`.
- Produces:
  - `DaySegment(start: datetime|None, end: datetime|None, hours: float, ob_eligible: bool = True)`, a frozen dataclass.
  - `segment_hours(segments: list[DaySegment]) -> float`
  - `segment_bounds(segments: list[DaySegment]) -> tuple[datetime|None, datetime|None]`
  - `segment_ob(segments: list[DaySegment], rules: list) -> dict[str, float]`

Why `start` and `end` are optional: `_apply_ot_display_shift` today keeps `ot_shift.hours` but sets `start` and `end` to `None` when `parse_ot_times` raises `ValueError` on malformed stored times. A segment must be able to carry hours without clock times or Task 4 would silently drop those hours.

- [ ] **Step 1: Write the failing test**

Create `tests/test_day_segments.py`:

```python
"""Unit tests for the day segment helpers.

These pin the three derive functions on their own. The proof that they preserve
period.py's behaviour is the characterization suite, not this file.
"""

import datetime
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ruff: noqa: E402
from app.core.schedule.ob import ObRule
from app.core.schedule.segments import (
    DaySegment,
    segment_bounds,
    segment_hours,
    segment_ob,
)

DAY = datetime.date(2026, 9, 14)  # a Monday


def _dt(hour, minute=0, day=DAY):
    return datetime.datetime.combine(day, datetime.time(hour, minute))


def _evening_rule():
    """OB1 18:00-24:00 on weekdays, the shape app/core/schedule/ob.py expects."""
    return ObRule(code="OB1", label="Kväll", start_time="18:00", end_time="24:00", rate=1.0, days=[0, 1, 2, 3, 4])


def test_empty_list_derives_the_off_day_scalars():
    assert segment_hours([]) == 0.0
    assert segment_bounds([]) == (None, None)
    assert segment_ob([], [_evening_rule()]) == {}


def test_hours_sum_across_segments():
    segments = [
        DaySegment(_dt(6), _dt(14, 30), 8.5),
        DaySegment(_dt(16), _dt(18), 2.0),
    ]
    assert segment_hours(segments) == 10.5


def test_bounds_span_earliest_start_to_latest_end():
    segments = [
        DaySegment(_dt(16), _dt(18), 2.0),
        DaySegment(_dt(6), _dt(14, 30), 8.5),
    ]
    assert segment_bounds(segments) == (_dt(6), _dt(18))


def test_segment_without_clock_times_keeps_its_hours_but_not_the_bounds():
    segments = [DaySegment(None, None, 8.5)]
    assert segment_hours(segments) == 8.5
    assert segment_bounds(segments) == (None, None)


def test_ob_sums_per_code_across_segments():
    rules = [_evening_rule()]
    segments = [
        DaySegment(_dt(18), _dt(20), 2.0),
        DaySegment(_dt(21), _dt(22), 1.0),
    ]
    assert segment_ob(segments, rules)["OB1"] == 3.0


def test_ob_ignores_segments_that_are_not_ob_eligible():
    rules = [_evening_rule()]
    segments = [DaySegment(_dt(0), _dt(0, 0, day=DAY + datetime.timedelta(days=1)), 24.0, ob_eligible=False)]
    assert segment_ob(segments, rules) == {}


def test_ob_of_one_eligible_segment_matches_calculate_ob_hours():
    from app.core.schedule.ob import calculate_ob_hours

    rules = [_evening_rule()]
    segment = DaySegment(_dt(14), _dt(22, 30), 8.5)
    assert segment_ob([segment], rules) == calculate_ob_hours(_dt(14), _dt(22, 30), rules)
```

- [ ] **Step 2: Confirm the ObRule constructor matches this test**

Run: `venv/bin/python3 -c "import inspect, app.core.schedule.ob as m; print(inspect.signature(m.ObRule))"`

If the field names or their order differ from the test's keyword arguments, fix the test's `_evening_rule` to match the real signature before continuing. Do not change `ob.py`.

- [ ] **Step 3: Run the test to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_day_segments.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'app.core.schedule.segments'`

- [ ] **Step 4: Write the module**

Create `app/core/schedule/segments.py`:

```python
# app/core/schedule/segments.py
"""A day's worked time as a list of intervals.

period.py resolves a day through a chain of sources (vacation, shift override,
swap, rotation, on-call override, overtime). Each of them used to rebind the same
four scalars (shift, hours, start, end), so a day could hold exactly one worked
interval. This module holds the list those scalars are derived from.

Deriving the scalars instead of storing them is what keeps the day dict
byte-identical, so summary.py, payslip.py, excel_shared.py, statistics.py and
api_v1.py need no change.
"""

import datetime
from dataclasses import dataclass

from app.core.schedule.ob import calculate_ob_hours


@dataclass(frozen=True)
class DaySegment:
    """One worked interval on a day.

    start and end are optional because a stored overtime row with unparseable
    times still contributes its hours, which is what the scalar version did.

    ob_eligible is False for on-call, which carries hours and a time span but no
    OB supplement: its compensation comes from the on-call rules instead.
    """

    start: datetime.datetime | None
    end: datetime.datetime | None
    hours: float
    ob_eligible: bool = True


def segment_hours(segments: list[DaySegment]) -> float:
    """Total worked hours across the segments."""
    return sum(segment.hours for segment in segments)


def segment_bounds(
    segments: list[DaySegment],
) -> tuple[datetime.datetime | None, datetime.datetime | None]:
    """The span the segments cover, as (earliest start, latest end).

    Segments without clock times are skipped, and a list holding only those
    yields (None, None) the way an empty list does.
    """
    timed = [s for s in segments if s.start is not None and s.end is not None]
    if not timed:
        return None, None
    return min(s.start for s in timed), max(s.end for s in timed)


def segment_ob(segments: list[DaySegment], rules: list) -> dict[str, float]:
    """OB hours per code, summed across every OB-bearing segment.

    Returns an empty dict when no segment qualifies, which is what the scalar
    version returned for OFF, SEM and on-call days. A day that does qualify gets
    every rule code back, zeros included, because calculate_ob_hours seeds its
    result from the rules it is given.
    """
    eligible = [s for s in segments if s.ob_eligible and s.start is not None and s.end is not None]
    if not eligible:
        return {}
    totals: dict[str, float] = {}
    for segment in eligible:
        for code, hours in calculate_ob_hours(segment.start, segment.end, rules).items():
            totals[code] = totals.get(code, 0.0) + hours
    return totals
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_day_segments.py -q`
Expected: 7 passed

- [ ] **Step 6: Lint**

Run: `venv/bin/python3 -m ruff check app/core/schedule/segments.py tests/test_day_segments.py && venv/bin/python3 -m ruff format --check app/core/schedule/segments.py tests/test_day_segments.py`
Expected: no findings. If `ruff format --check` fails, run `venv/bin/python3 -m ruff format app/core/schedule/segments.py tests/test_day_segments.py` and rerun.

- [ ] **Step 7: Commit**

```bash
git add app/core/schedule/segments.py tests/test_day_segments.py
git commit -m "$(cat <<'MSG'
refactor(schedule): add day segment helpers

A day's worked time is about to become a list of intervals instead of four
rebound scalars. This adds the type and the three derive functions on their
own, with nothing calling them yet.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MgsRgKYs94sawwQ12fmxMC
MSG
)"
```

---

### Task 2: `_resolve_effective_shift` returns segments

**Files:**
- Modify: `app/core/schedule/period.py:1703-1766` (`_ShiftResolution`, `_resolve_effective_shift`)
- Modify: `app/core/schedule/period.py:1479-1488` (call site in `_build_person_day_basic`)
- Modify: `app/core/schedule/period.py:1951-1958` (call site in `_populate_single_person_day`)

**Interfaces:**
- Consumes: `DaySegment`, `segment_hours`, `segment_bounds`, `segment_ob` from Task 1.
- Produces: `_ShiftResolution(shift, rotation_week, segments, ob)`, a 4-field NamedTuple. Both callers unpack four values and derive `hours`, `start`, `end` locally.

The arity drops from 6 to 4 on purpose: every unpack site fails loudly rather than silently binding the wrong names.

- [ ] **Step 1: Run the characterization suite to record the green baseline**

Run: `venv/bin/python3 -m pytest tests/test_period_characterization.py tests/test_summary_characterization.py tests/test_day_builder_agreement.py tests/test_api_v1_characterization.py -q`
Expected: 72 passed. If it is not green before you start, stop and report it, because the grader is broken and nothing after this is trustworthy.

- [ ] **Step 2: Add the import**

In `app/core/schedule/period.py`, add to the existing imports from `app.core.schedule`:

```python
from app.core.schedule.segments import DaySegment, segment_bounds, segment_hours, segment_ob
```

Place it with the other `app.core.schedule.*` imports at the top of the file, in alphabetical order among them.

- [ ] **Step 3: Replace the NamedTuple**

Replace:

```python
class _ShiftResolution(NamedTuple):
    shift: object
    rotation_week: object
    hours: float
    start: object
    end: object
    ob: dict
```

with:

```python
class _ShiftResolution(NamedTuple):
    shift: object
    rotation_week: object
    segments: list
    ob: dict
```

- [ ] **Step 4: Rewrite the four return points in `_resolve_effective_shift`**

Replace the inner helper:

```python
    def _with_ob(shift, rotation_week) -> _ShiftResolution:
        if shift is None:
            return _ShiftResolution(None, None, 0.0, None, None, {})
        hours, start, end = calculate_shift_hours(current_day, shift.code)
        ob = calculate_ob_hours(start, end, combined_ob_rules) if (start is not None and shift.code != "OC") else {}
        return _ShiftResolution(shift, rotation_week, hours, start, end, ob)
```

with:

```python
    def _with_ob(shift, rotation_week) -> _ShiftResolution:
        if shift is None:
            return _ShiftResolution(None, None, [], {})
        hours, start, end = calculate_shift_hours(current_day, shift.code)
        if start is None:
            return _ShiftResolution(shift, rotation_week, [], {})
        # On-call spans the whole day and carries hours, but its compensation comes
        # from the on-call rules, so it contributes no OB.
        segments = [DaySegment(start, end, hours, ob_eligible=shift.code != "OC")]
        return _ShiftResolution(shift, rotation_week, segments, segment_ob(segments, combined_ob_rules))
```

Then replace the two bare returns in the same function:

```python
            return _ShiftResolution(vacation_shift, rot[1], 0.0, None, None, {})
```
becomes
```python
            return _ShiftResolution(vacation_shift, rot[1], [], {})
```

and
```python
        return _ShiftResolution(None, None, 0.0, None, None, {})
```
becomes
```python
        return _ShiftResolution(None, None, [], {})
```

That last line appears twice in the file, at `period.py:1730` and `period.py:1764`. The one at 1730 sits inside `_with_ob` and is already gone once you replace the helper above, so exactly one occurrence is left to edit: the final return of `_resolve_effective_shift`. Verify with `grep -c "return _ShiftResolution(None, None, 0.0, None, None, {})" app/core/schedule/period.py`, which must print `0` when Step 4 is done.

- [ ] **Step 5: Update the call site in `_build_person_day_basic`**

Replace:

```python
    shift, rotation_week, hours, start, end, _ob = _resolve_effective_shift(
```
with:
```python
    shift, rotation_week, segments, _ob = _resolve_effective_shift(
```

Leave the argument list untouched. Immediately after that call's closing `)`, the local names `hours`, `start` and `end` no longer exist; Task 3 and Task 4 rewrite the two overlay calls that use them, and the `return` dict at the end of the function is fixed in Task 4 Step 5. Until then this function does not run, which is why Steps 6 and 7 below expect failures.

- [ ] **Step 6: Update the call site in `_populate_single_person_day`**

Replace:

```python
    shift, rotation_week, hours, start, end, ob = _resolve_effective_shift(
```
with:
```python
    shift, rotation_week, segments, ob = _resolve_effective_shift(
```

- [ ] **Step 7: Run the characterization suite to confirm it fails loudly**

Run: `venv/bin/python3 -m pytest tests/test_period_characterization.py -q`
Expected: FAIL with `NameError: name 'hours' is not defined` (or `start`/`end`) raised from `_apply_oncall_override`'s call site. A failure naming any other cause means a return point was missed in Step 4.

Do not commit a red tree. Tasks 3 and 4 close this; they are separate tasks so a reviewer can judge each overlay on its own, but they must land before the branch is pushed.

---

### Task 3: `_apply_oncall_override` operates on segments

**Files:**
- Modify: `app/core/schedule/period.py:1845-1867` (`_apply_oncall_override`)
- Modify: `app/core/schedule/period.py` (its two call sites, one in each day builder)

**Interfaces:**
- Consumes: `_ShiftResolution.segments` from Task 2.
- Produces: `_apply_oncall_override(override, shift, segments, ob, shift_types) -> tuple[object, list, dict]`, returning `(shift, segments, ob)`.

Both override branches previously returned `(shift, 0.0, None, None, {})`. Zero hours with no clock times and no OB is exactly what an empty segment list derives to, so both become `[]`.

- [ ] **Step 1: Rewrite the function**

Replace:

```python
def _apply_oncall_override(override, shift, hours, start, end, ob, shift_types):
    """Apply a manual on-call override to the day's shift.

    ADD replaces the shift with OC; REMOVE turns an OC shift into OFF. Returns the (possibly
    modified) (shift, hours, start, end, ob); unchanged when there is no override.
    """
    if override is None:
        return shift, hours, start, end, ob

    from app.database.database import OnCallOverrideType

    if override.override_type == OnCallOverrideType.ADD:
        oc_shift = next((s for s in shift_types if s.code == "OC"), None)
        if oc_shift:
            return oc_shift, 0.0, None, None, {}  # OC har inga specifika tider
    elif override.override_type == OnCallOverrideType.REMOVE:
        if shift and shift.code == "OC":
            off_shift = next((s for s in shift_types if s.code == "OFF"), None)
            if off_shift:
                return off_shift, 0.0, None, None, {}

    return shift, hours, start, end, ob
```

with:

```python
def _apply_oncall_override(override, shift, segments, ob, shift_types):
    """Apply a manual on-call override to the day's shift.

    ADD replaces the shift with OC; REMOVE turns an OC shift into OFF. Returns the (possibly
    modified) (shift, segments, ob); unchanged when there is no override.

    Both overrides clear the segment list. An overridden day carries no worked
    interval, which is what the scalar version expressed as zero hours with no
    start, no end and no OB.
    """
    if override is None:
        return shift, segments, ob

    from app.database.database import OnCallOverrideType

    if override.override_type == OnCallOverrideType.ADD:
        oc_shift = next((s for s in shift_types if s.code == "OC"), None)
        if oc_shift:
            return oc_shift, [], {}  # OC har inga specifika tider
    elif override.override_type == OnCallOverrideType.REMOVE:
        if shift and shift.code == "OC":
            off_shift = next((s for s in shift_types if s.code == "OFF"), None)
            if off_shift:
                return off_shift, [], {}

    return shift, segments, ob
```

- [ ] **Step 2: Update the call site in `_build_person_day_basic`**

Replace:

```python
    shift, hours, start, end, _ob = _apply_oncall_override(oncall_override, shift, hours, start, end, _ob, shift_types)
```
with:
```python
    shift, segments, _ob = _apply_oncall_override(oncall_override, shift, segments, _ob, shift_types)
```

- [ ] **Step 3: Update the call site in `_populate_single_person_day`**

Replace:

```python
    shift, hours, start, end, ob = _apply_oncall_override(oncall_override, shift, hours, start, end, ob, shift_types)
```
with:
```python
    shift, segments, ob = _apply_oncall_override(oncall_override, shift, segments, ob, shift_types)
```

- [ ] **Step 4: Confirm the remaining failure is only the overtime overlay**

Run: `venv/bin/python3 -m pytest tests/test_period_characterization.py -q 2>&1 | tail -20`
Expected: still failing, and every traceback now points at the `_apply_ot_display_shift` call site. If any traceback still names `_apply_oncall_override`, a call site was missed.

Run: `venv/bin/python3 -c "import re,pathlib; s=pathlib.Path('app/core/schedule/period.py').read_text(); print(len(re.findall(r'_apply_oncall_override\(', s)))"`
Expected: `3` (the definition plus two call sites).

---

### Task 4: The overtime overlay replaces segments, and the scalars are derived back

**Files:**
- Modify: `app/core/schedule/period.py:1399-1410` (`_apply_ot_display_shift`)
- Modify: `app/core/schedule/period.py` (its two call sites)
- Modify: `app/core/schedule/period.py:1505-1516` (the `return` dict in `_build_person_day_basic`)
- Modify: `app/core/schedule/period.py:2010-2050` (the `day_info.update` in `_populate_single_person_day`)

**Interfaces:**
- Consumes: `_apply_oncall_override`'s `(shift, segments, ob)` from Task 3.
- Produces: `_apply_ot_display_shift(ot_shift, date, shift, segments, shift_types) -> tuple[object, list]`, returning `(shift, segments)`. `ob` is deliberately absent: the scalar version did not recompute OB after the overtime overlay either, so a called-in overtime day keeps the OB of the shift it replaced. Preserving that is the point of this branch. Changing it is a behaviour change and belongs to branch 1 at the earliest.

- [ ] **Step 1: Rewrite the function**

Replace:

```python
def _apply_ot_display_shift(ot_shift, date: datetime.date, shift, hours, start, end, shift_types):
    """Replace the day's shift with OT for display, unless the overtime only extends it."""
    if ot_shift.is_extension:
        return shift, hours, start, end
    ot_shift_type = next((s for s in shift_types if s.code == "OT"), None)
    if not ot_shift_type:
        return shift, hours, start, end
    try:
        ot_start, ot_end = parse_ot_times(ot_shift, date)
    except ValueError:
        ot_start, ot_end = None, None
    return ot_shift_type, ot_shift.hours, ot_start, ot_end
```

with:

```python
def _apply_ot_display_shift(ot_shift, date: datetime.date, shift, segments, shift_types):
    """Replace the day's worked time with OT for display, unless the overtime only extends it.

    The caller's OB is left alone on purpose: a called-in overtime day keeps the OB
    of the shift it replaced, which is what the scalar version did.

    Unparseable stored times still yield a segment, carrying the hours without
    clock times, so the day does not silently lose them.
    """
    if ot_shift.is_extension:
        return shift, segments
    ot_shift_type = next((s for s in shift_types if s.code == "OT"), None)
    if not ot_shift_type:
        return shift, segments
    try:
        ot_start, ot_end = parse_ot_times(ot_shift, date)
    except ValueError:
        ot_start, ot_end = None, None
    return ot_shift_type, [DaySegment(ot_start, ot_end, ot_shift.hours, ob_eligible=False)]
```

- [ ] **Step 2: Update the call site in `_build_person_day_basic`**

Replace:

```python
        shift, hours, start, end = _apply_ot_display_shift(ot_shift, date, shift, hours, start, end, shift_types)
```
with:
```python
        shift, segments = _apply_ot_display_shift(ot_shift, date, shift, segments, shift_types)
```

- [ ] **Step 3: Update the call site in `_populate_single_person_day`**

Replace:

```python
        shift, hours, start, end = _apply_ot_display_shift(ot_shift, current_day, shift, hours, start, end, shift_types)
```
with:
```python
        shift, segments = _apply_ot_display_shift(ot_shift, current_day, shift, segments, shift_types)
```

- [ ] **Step 4: Derive the scalars in `_populate_single_person_day`**

Immediately before the `day_info.update({` call at the end of the function, insert:

```python
    # The day dict still speaks in scalars, so the segment list collapses here and
    # nowhere else. Every consumer outside this module is unchanged.
    hours = segment_hours(segments)
    start, end = segment_bounds(segments)
```

The `day_pay_override` block sits above this point and only touches `oncall_pay`, `oncall_details` and `ob_hours_override`, so it is unaffected. Leave it where it is.

- [ ] **Step 5: Derive the scalars in `_build_person_day_basic`**

Immediately before the final `return {` of that function, insert:

```python
    hours = segment_hours(segments)
    start, end = segment_bounds(segments)
```

The returned dict keeps `"hours": hours, "start": start, "end": end` exactly as written.

- [ ] **Step 6: Run the characterization suite**

Run: `venv/bin/python3 -m pytest tests/test_period_characterization.py tests/test_summary_characterization.py tests/test_day_builder_agreement.py tests/test_api_v1_characterization.py -q`
Expected: 72 passed.

A failure here is a real behaviour change, not a test to adjust. Read the assertion diff and find which return point stopped matching. The four likeliest causes, in order: a missed `_ShiftResolution` return point still passing `0.0, None, None`; `ob_eligible` set on the on-call segment; OB recomputed after the overtime overlay instead of left alone; the derive lines placed above the overtime overlay rather than below it.

- [ ] **Step 7: Run the full suite**

Run: `venv/bin/python3 -m pytest -q 2>&1 | tail -15`
Expected: the same pass count as on `main`. Capture that number first with `git stash && venv/bin/python3 -m pytest -q 2>&1 | tail -3 && git stash pop` if you need the baseline.

- [ ] **Step 8: Lint**

Run: `venv/bin/python3 -m ruff check . && venv/bin/python3 -m ruff format --check .`
Expected: no findings.

- [ ] **Step 9: Commit**

```bash
git add app/core/schedule/period.py
git commit -m "$(cat <<'MSG'
refactor(schedule): carry a day's worked time as a list of segments

_resolve_effective_shift and the on-call and overtime overlays passed four
scalars (shift, hours, start, end) from one to the next, so a day could hold
exactly one worked interval. They now pass a list of DaySegment, and the two
day builders derive the scalars back immediately before writing the day dict.

No output changes. The day dict keys are identical, so summary, payslip, the
Excel export, statistics and the v1 API are untouched, and the four
characterization suites pass unmodified.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MgsRgKYs94sawwQ12fmxMC
MSG
)"
```

---

### Task 5: Prove nothing else moved

**Files:**
- Modify: none. This task only verifies.

**Interfaces:**
- Consumes: the finished branch.
- Produces: nothing. It is the gate.

- [ ] **Step 1: Prove no test file was edited**

Run: `git diff --name-only main...HEAD -- tests/`
Expected: exactly one line, `tests/test_day_segments.py`, the new file from Task 1. Any other path means the refactor changed behaviour and was papered over. Revert that test file and fix the source instead.

- [ ] **Step 2: Prove no consumer outside the schedule core was touched**

Run: `git diff --name-only main...HEAD`
Expected exactly these five paths, no more:

```
app/core/schedule/period.py
app/core/schedule/segments.py
docs/superpowers/plans/2026-09-14-day-segments-refactor.md
docs/superpowers/specs/2026-09-12-day-time-segments-design.md
tests/test_day_segments.py
```

The spec and the plan are both committed on this branch, ahead of Task 1. Anything under `app/routes/`, `app/templates/` or `app/core/schedule/summary.py` does not belong here.

- [ ] **Step 3: Prove the scalars are gone from the overlay chain**

Run: `venv/bin/python3 -c "import pathlib,re; s=pathlib.Path('app/core/schedule/period.py').read_text(); print([l.strip() for l in s.splitlines() if re.search(r'shift, hours, start, end', l)])"`
Expected: `[]`. A remaining hit is a call site that was edited by hand into something that still compiles but bypasses the segment list.

- [ ] **Step 4: Spot-check a real day against main**

Run:

```bash
venv/bin/python3 -c "
import datetime, json
from app.core.schedule.period import generate_month_data
days = generate_month_data(2026, 6, person_id=3)
print(json.dumps([{k: str(v) for k, v in sorted(d.items())} for d in days], indent=0))
" > /tmp/claude-1000/-home-kakan-workspace-Periodical-dev/526e5643-be84-4348-a705-99a881369283/scratchpad/after.txt
git stash
venv/bin/python3 -c "
import datetime, json
from app.core.schedule.period import generate_month_data
days = generate_month_data(2026, 6, person_id=3)
print(json.dumps([{k: str(v) for k, v in sorted(d.items())} for d in days], indent=0))
" > /tmp/claude-1000/-home-kakan-workspace-Periodical-dev/526e5643-be84-4348-a705-99a881369283/scratchpad/before.txt
git stash pop
diff /tmp/claude-1000/-home-kakan-workspace-Periodical-dev/526e5643-be84-4348-a705-99a881369283/scratchpad/before.txt /tmp/claude-1000/-home-kakan-workspace-Periodical-dev/526e5643-be84-4348-a705-99a881369283/scratchpad/after.txt && echo IDENTICAL
```

Expected: `IDENTICAL`.

`generate_month_data` returns a `list[dict]`, one entry per day, so the snippet dumps the whole month rather than a single day. The point of the check is the diff, not the exact argument list.

- [ ] **Step 5: Report**

State the pass count of the full suite, the output of Step 1 and the result of Step 4. Do not claim the refactor is behaviour-preserving without those three.

---

## What this branch does not do

Branch 1 (`feat/day-segment-types`) builds on this: the `kind` and `side` columns, overtime before a shift, extra time with OB per `wage_type`, the custom `ETC` shift, the tabbed edit panel, and the routes taking `dates` lists. None of it belongs here. If a task in this plan tempts you toward any of it, the answer is no: this branch's whole value is that it changes nothing observable.
