# Day Segment Types Implementation Plan (branch 1a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a day carry overtime before the shift, non-overtime extra time on either side, and a custom labelled shift, with the pay falling out of machinery that already exists.

**Architecture:** `overtime_shifts` gains `kind` (`ot`/`extra`) and `side` (`before`/`after`/`full`) and drops `is_extension`, so it holds several rows per day instead of one. `shift_overrides` gains nullable `start_time`, `end_time` and `label` so shift code `ETC` renders a custom shift. `period.py` reads all of a day's overtime rows instead of one, appends only `extra` rows to the segment list branch 0 introduced, and keeps overtime reported through `ot_hours`/`ot_pay`/`ot_details` exactly as today. No UI: branch 1b makes this reachable from a browser.

**Tech Stack:** Python 3.14, SQLAlchemy, SQLite 3.53.1, pytest, ruff. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-12-day-time-segments-design.md`

**Depends on:** branch `refactor/day-segments` (merged or as this branch's parent). `DaySegment`, `segment_hours`, `segment_bounds` and `segment_ob` must exist in `app/core/schedule/segments.py` before Task 3.

## Global Constraints

- **Branch from `refactor/day-segments`, not `main`.** That branch is unmerged; this work builds on its segment list.
- **`day_worked_hours` counts `day["hours"] + day["ot_hours"]`.** Only `extra` rows may join the segment list. Putting an overtime row in both places reports an 8.5 h shift with 2 h overtime as 12.5 h instead of 10.5. This is the single most important rule in this plan.
- **`is_extension` leaves the database but stays in the output.** `api_v1.py:183` returns it in JSON and `excel_shared.py:125` plus `breakdown_table.html:138` read `ot_details["is_extension"]`. It becomes the derived value `side != "full"`.
- **Pay needs no new branch on `wage_type`.** `summary.py:361-374` already prices `total_hours - ot_hours - substitute_hours` at the hourly rate for `HOURLY` users, and `MONTHLY` gross is the fixed salary. Extra time entering `day["hours"]` is therefore paid correctly for both without new code. Do not add a `wage_type` check.
- **Migration is two pieces.** `migrations/migrate_schema.py` adds columns from the models by itself. The backfill, the indexes and the column drop are a one-off data migration in its own file, which is the convention that script's own docstring sets.
- **Prod runs migrations by hand.** `scripts/deploy.sh` does not run them. The new script must be safe to run twice.
- **All source code comments in English.** Do not translate the Swedish comments already in `period.py`.
- **No em dash (—) anywhere,** including code comments and commit messages.
- **Run from the repo root** using `venv/bin/python3`.

---

### Task 1: Model columns and the schema migration

**Files:**
- Modify: `app/database/database.py:207-232` (`OvertimeShift`)
- Modify: `app/database/database.py:298-315` (`ShiftOverride`)
- Test: `tests/test_segment_columns.py`

**Interfaces:**
- Produces: `OvertimeShift.kind: str` (`"ot"`/`"extra"`, NOT NULL, default `"ot"`), `OvertimeShift.side: str` (`"before"`/`"after"`/`"full"`, NOT NULL, default `"full"`); `ShiftOverride.start_time`, `ShiftOverride.end_time` (nullable `Time`), `ShiftOverride.label` (nullable `String(40)`).

`is_extension` is NOT dropped from the model in this task. Task 2's data migration drops the database column, and Task 3 removes the last code that reads it. Dropping the model attribute now would break `period.py` before its replacement exists.

- [ ] **Step 1: Write the failing test**

Create `tests/test_segment_columns.py`:

```python
"""The new columns exist with the shape migrate_schema.py can add unaided."""

import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database.database import Base  # noqa: E402


def _columns(table):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return {c["name"]: c for c in inspect(engine).get_columns(table)}


def test_overtime_kind_and_side_are_not_null_with_defaults():
    cols = _columns("overtime_shifts")
    assert cols["kind"]["nullable"] is False
    assert cols["side"]["nullable"] is False


def test_shift_override_gains_nullable_times_and_label():
    cols = _columns("shift_overrides")
    for name in ("start_time", "end_time", "label"):
        assert cols[name]["nullable"] is True


def test_migrate_schema_can_add_the_new_columns_unaided():
    """A NOT NULL column needs a Python-side default or migrate_schema raises."""
    from migrations.migrate_schema import add_column_sql

    table = Base.metadata.tables["overtime_shifts"]
    for name in ("kind", "side"):
        sql = add_column_sql("overtime_shifts", table.columns[name])
        assert "NOT NULL" in sql
        assert "DEFAULT" in sql
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_segment_columns.py -q`
Expected: FAIL with `KeyError: 'kind'`

- [ ] **Step 3: Add the columns**

In `app/database/database.py`, inside `class OvertimeShift`, directly below the `is_extension` line:

```python
    # "ot" or "extra". Overtime is paid at the OT rate and reported through
    # ot_hours/ot_pay; extra time is unpaid-at-source worked time that joins the
    # day's segment list and earns OB on its own interval.
    kind = Column(String(8), default="ot", nullable=False)
    # "before" or "after" the day's shift, or "full" for a called-in shift that
    # replaces it. Never NULL: SQLite treats NULLs in a unique index as distinct,
    # so a NULL side would leave called-in overtime unconstrained.
    side = Column(String(6), default="full", nullable=False)
```

In `class ShiftOverride`, directly below the `shift_code` line:

```python
    # Set only for shift_code "ETC", the custom labelled shift. The resolver
    # builds a synthetic shift type from these three.
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    label = Column(String(40), nullable=True)
```

`Time` and `String` are already imported at the top of the file. Verify with `grep -n "^from sqlalchemy" app/database/database.py` and add `Time` to that import list if it is missing.

- [ ] **Step 4: Run the test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_segment_columns.py -q`
Expected: 3 passed

- [ ] **Step 5: Confirm nothing else broke**

Run: `venv/bin/python3 -m pytest tests/test_migrate_schema.py tests/test_period_characterization.py -q`
Expected: all passed. New columns with defaults are invisible to code that does not read them.

- [ ] **Step 6: Lint and commit**

```bash
venv/bin/python3 -m ruff check app/database/database.py tests/test_segment_columns.py
venv/bin/python3 -m ruff format --check app/database/database.py tests/test_segment_columns.py
git add app/database/database.py tests/test_segment_columns.py
git commit -m "$(cat <<'MSG'
feat(db): add kind and side to overtime shifts, times and label to overrides

kind and side let one day hold several overtime rows instead of one, and let a
row describe non-overtime extra time. The override columns carry the custom
labelled shift. Nothing reads them yet.

side defaults to "full" rather than NULL because SQLite treats NULLs in a unique
index as distinct, which would leave called-in overtime unconstrained.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MgsRgKYs94sawwQ12fmxMC
MSG
)"
```

---

### Task 2: The data migration

**Files:**
- Create: `migrations/migrate_ot_kind_side.py`
- Test: `tests/test_migrate_ot_kind_side.py`

**Interfaces:**
- Consumes: the columns from Task 1.
- Produces: `migrate(engine) -> dict[str, int]`, returning counts keyed `backfilled`, `indexes_created`, `column_dropped` (0 or 1). Idempotent: a second run returns zeros and changes nothing.

- [ ] **Step 1: Write the failing test**

Create `tests/test_migrate_ot_kind_side.py`:

```python
"""Checks for the one-off data migration that backfills kind/side and drops is_extension."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

sys.path.insert(0, str(Path(__file__).parent.parent))

from migrations.migrate_ot_kind_side import migrate  # noqa: E402

# The pre-migration shape: is_extension present, kind/side present but unset.
LEGACY_DDL = """
CREATE TABLE overtime_shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    substitute_id INTEGER,
    date DATE NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    hours FLOAT NOT NULL,
    ot_pay FLOAT NOT NULL,
    is_extension BOOLEAN NOT NULL DEFAULT 0,
    created_at DATETIME,
    created_by INTEGER,
    kind VARCHAR(8) NOT NULL DEFAULT 'ot',
    side VARCHAR(6) NOT NULL DEFAULT 'full'
)
"""


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'ot.db'}")
    with eng.begin() as c:
        c.exec_driver_sql(LEGACY_DDL)
        c.exec_driver_sql(
            "INSERT INTO overtime_shifts (user_id, date, start_time, end_time, hours, ot_pay, is_extension)"
            " VALUES (1, '2026-06-01', '22:30', '00:30', 2.0, 600.0, 1),"
            "        (1, '2026-06-02', '14:00', '22:30', 8.5, 2550.0, 0)"
        )
    return eng


def _rows(engine):
    with engine.begin() as c:
        return {r[0]: (r[1], r[2]) for r in c.execute(text("SELECT date, kind, side FROM overtime_shifts"))}


def test_extension_becomes_ot_after_and_called_in_becomes_ot_full(engine):
    migrate(engine)
    rows = _rows(engine)
    assert rows["2026-06-01"] == ("ot", "after")
    assert rows["2026-06-02"] == ("ot", "full")


def test_is_extension_column_is_dropped(engine):
    migrate(engine)
    assert "is_extension" not in {c["name"] for c in inspect(engine).get_columns("overtime_shifts")}


def test_unique_index_rejects_a_duplicate_kind_and_side(engine):
    migrate(engine)
    with pytest.raises(Exception):
        with engine.begin() as c:
            c.exec_driver_sql(
                "INSERT INTO overtime_shifts (user_id, date, start_time, end_time, hours, ot_pay, kind, side)"
                " VALUES (1, '2026-06-01', '05:00', '06:00', 1.0, 0.0, 'ot', 'after')"
            )


def test_two_substitute_rows_on_one_day_are_also_constrained(engine):
    migrate(engine)
    with engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO overtime_shifts (substitute_id, date, start_time, end_time, hours, ot_pay, kind, side)"
            " VALUES (7, '2026-06-03', '14:00', '22:30', 8.5, 0.0, 'ot', 'full')"
        )
    with pytest.raises(Exception):
        with engine.begin() as c:
            c.exec_driver_sql(
                "INSERT INTO overtime_shifts (substitute_id, date, start_time, end_time, hours, ot_pay, kind, side)"
                " VALUES (7, '2026-06-03', '06:00', '14:30', 8.5, 0.0, 'ot', 'full')"
            )


def test_running_twice_changes_nothing(engine):
    first = migrate(engine)
    second = migrate(engine)
    assert first["backfilled"] == 2
    assert second == {"backfilled": 0, "indexes_created": 0, "column_dropped": 0}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_migrate_ot_kind_side.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'migrations.migrate_ot_kind_side'`

- [ ] **Step 3: Write the migration**

Create `migrations/migrate_ot_kind_side.py`:

```python
#!/usr/bin/env python3
"""Backfill overtime_shifts.kind/side from is_extension, then drop is_extension.

migrate_schema.py adds the two columns from the models, but it never rewrites
existing rows and never drops anything, so this is the data half and lives in its
own file the way that script's docstring prescribes.

Three steps, each skipped when it has already happened, so the script is safe to
run twice and safe to run on a database that has only ever known the new shape:

1. Rows still carrying the column default get kind/side from is_extension.
2. Two unique indexes, one per owner column, enforce one row per
   (owner, date, kind, side). Each is a no-op for rows where its owner column is
   NULL, which is what we want since exactly one of the two is ever set.
3. is_extension goes. SQLite 3.35+ supports ALTER TABLE DROP COLUMN.

Usage:
    python migrations/migrate_ot_kind_side.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402

INDEXES = {
    "ix_ot_user_day_kind_side": "CREATE UNIQUE INDEX ix_ot_user_day_kind_side"
    " ON overtime_shifts (user_id, date, kind, side)",
    "ix_ot_sub_day_kind_side": "CREATE UNIQUE INDEX ix_ot_sub_day_kind_side"
    " ON overtime_shifts (substitute_id, date, kind, side)",
}


def migrate(engine) -> dict[str, int]:
    """Apply the three steps. Returns what each one actually did."""
    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("overtime_shifts")}
    existing_indexes = {i["name"] for i in inspector.get_indexes("overtime_shifts")}

    result = {"backfilled": 0, "indexes_created": 0, "column_dropped": 0}

    with engine.begin() as connection:
        if "is_extension" in columns:
            backfill = connection.execute(
                text(
                    "UPDATE overtime_shifts"
                    " SET kind = 'ot', side = CASE WHEN is_extension THEN 'after' ELSE 'full' END"
                )
            )
            result["backfilled"] = backfill.rowcount

        for name, ddl in INDEXES.items():
            if name not in existing_indexes:
                connection.exec_driver_sql(ddl)
                result["indexes_created"] += 1

        if "is_extension" in columns:
            connection.exec_driver_sql('ALTER TABLE overtime_shifts DROP COLUMN "is_extension"')
            result["column_dropped"] = 1

    return result


def main() -> int:
    from app.database.database import DATABASE_URL, engine

    print(f"Migrating {DATABASE_URL}")
    result = migrate(engine)
    print(
        f"  rows backfilled:  {result['backfilled']}\n"
        f"  indexes created:  {result['indexes_created']}\n"
        f"  is_extension dropped: {bool(result['column_dropped'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `venv/bin/python3 -m pytest tests/test_migrate_ot_kind_side.py -q`
Expected: 5 passed

If `test_running_twice_changes_nothing` fails on the backfill count, the first run reported the wrong `rowcount`; SQLite reports rows matched by `UPDATE`, which is every row, so 2 is correct for the fixture.

- [ ] **Step 5: Lint and commit**

```bash
venv/bin/python3 -m ruff check migrations/migrate_ot_kind_side.py tests/test_migrate_ot_kind_side.py
venv/bin/python3 -m ruff format --check migrations/migrate_ot_kind_side.py tests/test_migrate_ot_kind_side.py
git add migrations/migrate_ot_kind_side.py tests/test_migrate_ot_kind_side.py
git commit -m "$(cat <<'MSG'
feat(db): backfill overtime kind/side and drop is_extension

Backfills the two new columns from is_extension, adds one unique index per owner
column so a day holds at most one row per (kind, side), then drops is_extension.
Idempotent, so it is safe to run twice and safe on a fresh database.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MgsRgKYs94sawwQ12fmxMC
MSG
)"
```

---

### Task 3: `period.py` reads every overtime row for a day

**Files:**
- Modify: `app/core/schedule/overtime.py:25-37` (`get_overtime_shift_for_date`)
- Modify: `app/core/schedule/period.py:1205-1230` (`_batch_fetch_ot_shifts`)
- Modify: `app/core/schedule/period.py:1369-1397` (`_lookup_ot_shifts`, `_lookup_ot_shift`)
- Modify: `app/core/schedule/period.py:1399-1417` (`_apply_ot_display_shift`)
- Modify: `app/core/schedule/period.py` (both day builders)
- Test: `tests/test_day_segment_kinds.py`

**Interfaces:**
- Consumes: `DaySegment`, `segment_ob` from `app.core.schedule.segments`.
- Produces:
  - `get_overtime_rows_for_date(session, user_id, date) -> list` replaces `get_overtime_shift_for_date`. The old name stays as a one-line wrapper returning the `side == "full"` row or the first row, because `app/core/schedule/__init__.py:37` exports it and `api_v1.py` imports it.
  - `_batch_fetch_ot_shifts` maps `(person_id, date) -> list`, not a single row.
  - `_apply_ot_display_shift(ot_rows, date, shift, segments, shift_types) -> tuple[object, list]` takes the row list.
  - `extra_segments(ot_rows, date) -> list[DaySegment]` in `segments.py`, turning `kind == "extra"` rows into segments.

This is the task where the double-count rule bites. `extra_segments` feeds the segment list; overtime rows never do.

- [ ] **Step 1: Write the failing test**

Create `tests/test_day_segment_kinds.py`. The fixture is a trimmed copy of the
one in `tests/test_period_characterization.py`, with its own in-memory database
URL so the two files cannot collide:

```python
"""How a day's overtime and extra-time rows reach the day dict.

Person 1 works N2 (14:00-22:30, 8.5 h) on 2026-03-02 under the era below.
"""

import datetime
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

# ruff: noqa: E402
import app.database.database as db_module
from app.core.schedule import clear_schedule_cache
from app.core.schedule.period import generate_month_data
from app.database.database import (
    Absence,
    AbsenceType,
    Base,
    OvertimeShift,
    RotationEra,
    User,
    UserRole,
    WageType,
)

TEST_DB_URL = "sqlite:///file:test_segment_kinds_memdb?mode=memory&cache=shared&uri=true"
test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False, "uri": True})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

ERA_PATTERN = {
    "1": ["OFF", "OFF", "OFF", "N3", "N3", "N3", "N3"],
    "2": ["OFF", "OC", "N3", "N3", "N3", "N3", "OFF"],
    "3": ["OFF", "OFF", "N1", "N1", "N1", "N1", "OC"],
    "4": ["OC", "OFF", "N2", "N2", "N2", "OFF", "N1"],
    "5": ["N1", "N1", "N1", "N1", "OC", "OFF", "OFF"],
    "6": ["N3", "N3", "N3", "OFF", "OFF", "OC", "N3"],
    "7": ["N3", "N3", "OFF", "OC", "N2", "N2", "N2"],
    "8": ["N2", "N2", "OFF", "OFF", "N1", "N1", "N1"],
    "9": ["N1", "N1", "OC", "OFF", "OFF", "N2", "N2"],
    "10": ["N2", "N2", "N2", "N2", "OFF", "OFF", "OFF"],
}

# An N2 day (14:00-22:30, 8.5 h) for person 1 under ERA_PATTERN, which is why every
# expected number below is 8.5-based. test_the_baseline_day_is_n2 is the guard: if it
# fails, the pattern moved, and 2026-03-01 is the fallback, since
# test_period_characterization.py pins that date as N2 under this exact pattern.
TARGET = datetime.date(2026, 3, 2)


@pytest.fixture
def seg_session(monkeypatch):
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)
    clear_schedule_cache()

    session = TestSessionLocal()
    session.query(RotationEra).delete()
    session.add(
        RotationEra(
            start_date=datetime.date(2026, 1, 2),
            end_date=None,
            rotation_length=10,
            weeks_pattern=ERA_PATTERN,
        )
    )
    session.add(
        User(
            id=1,
            username="seguser",
            password_hash="x",
            name="Segments",
            role=UserRole.USER,
            wage=30000,
            wage_type=WageType.MONTHLY,
            person_id=1,
            tax_table="33",
            vacation={},
            must_change_password=0,
        )
    )
    session.commit()

    yield session

    session.close()
    clear_schedule_cache()
    Base.metadata.drop_all(bind=test_engine)


def _day(session, date=TARGET):
    clear_schedule_cache()
    days = generate_month_data(date.year, date.month, 1, session=session)
    return next(d for d in days if d["date"] == date)


def _add(session, **kw):
    """Insert one overtime_shifts row on TARGET for person 1."""
    session.add(
        OvertimeShift(
            user_id=1,
            date=kw.get("date", TARGET),
            start_time=datetime.time.fromisoformat(kw["start"]),
            end_time=datetime.time.fromisoformat(kw["end"]),
            hours=kw["hours"],
            ot_pay=0.0,
            kind=kw["kind"],
            side=kw["side"],
        )
    )
    session.commit()


@pytest.fixture
def day_for(seg_session):
    def build(**kw):
        _add(seg_session, **kw)
        return _day(seg_session)

    return build


@pytest.fixture
def day_for_many(seg_session):
    def build(rows):
        for row in rows:
            _add(seg_session, **row)
        return _day(seg_session)

    return build


@pytest.fixture
def day_for_vacation(seg_session):
    def build(**kw):
        seg_session.add(Absence(user_id=1, date=TARGET, absence_type=AbsenceType.VACATION))
        seg_session.commit()
        _add(seg_session, **kw)
        return _day(seg_session)

    return build


def test_the_baseline_day_is_n2(seg_session):
    """Guards every expected number below. If this fails, TARGET moved."""
    day = _day(seg_session)
    assert day["shift"].code == "N2"
    assert day["hours"] == 8.5


def test_extra_time_adds_hours_and_ob_but_no_ot_pay(day_for):
    """One hour of extra time before an N2 shift: 9.5 worked hours, no OT pay."""
    day = day_for(kind="extra", side="before", start="13:00", end="14:00", hours=1.0)
    assert day["hours"] == 9.5
    assert day["ot_pay"] == 0.0
    assert day["ot_hours"] == 0.0


def test_overtime_after_keeps_hours_out_of_the_segment_list(day_for):
    """The 8.5 h shift stays 8.5 in day["hours"]; the 2 h live in ot_hours alone.

    day_worked_hours adds the two, so a row counted in both would report 12.5.
    """
    from app.core.schedule.summary import day_worked_hours

    day = day_for(kind="ot", side="after", start="22:30", end="00:30", hours=2.0)
    assert day["hours"] == 8.5
    assert day["ot_hours"] == 2.0
    assert day_worked_hours(day) == 10.5


def test_overtime_before_and_after_on_the_same_day_both_pay(day_for_many):
    day = day_for_many(
        [
            dict(kind="ot", side="before", start="05:00", end="06:00", hours=1.0),
            dict(kind="ot", side="after", start="22:30", end="00:30", hours=2.0),
        ]
    )
    assert day["ot_hours"] == 3.0
    assert day["ot_pay"] > 0


def test_ot_details_reports_the_after_row(day_for_many):
    """summary.py splits OT across midnight from ot_details, and only the
    after row can cross midnight."""
    day = day_for_many(
        [
            dict(kind="ot", side="before", start="05:00", end="06:00", hours=1.0),
            dict(kind="ot", side="after", start="22:30", end="00:30", hours=2.0),
        ]
    )
    assert day["ot_details"]["start_time"].startswith("22:30")
    assert day["ot_details"]["is_extension"] is True


def test_called_in_overtime_still_replaces_the_shift(day_for):
    day = day_for(kind="ot", side="full", start="14:00", end="22:30", hours=8.5)
    assert day["shift"].code == "OT"
    assert day["ot_details"]["is_extension"] is False


def test_a_vacation_day_still_suppresses_overtime_pay(day_for_vacation):
    """Issue #285: the vacation guard outranks every overtime row."""
    day = day_for_vacation(kind="ot", side="after", start="22:30", end="00:30", hours=2.0)
    assert day["ot_pay"] == 0.0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_day_segment_kinds.py -q`
Expected: FAIL. Before the implementation, `_lookup_ot_shifts` returns one row, so the two-row tests cannot pass.

- [ ] **Step 3: Add `extra_segments` to `segments.py`**

```python
def extra_segments(ot_rows: list, date: datetime.date) -> list[DaySegment]:
    """Turn a day's kind == "extra" rows into segments.

    Overtime rows are deliberately excluded. summary.day_worked_hours adds
    day["hours"] and day["ot_hours"], so an overtime row appearing in both would
    be counted twice: an 8.5 hour shift with 2 hours of overtime would report
    12.5 instead of 10.5.
    """
    from app.core.time_utils import parse_ot_times

    segments = []
    for row in ot_rows:
        if row.kind != "extra":
            continue
        try:
            start, end = parse_ot_times(row, date)
        except ValueError:
            start, end = None, None
        segments.append(DaySegment(start, end, row.hours, ob_eligible=True))
    return segments
```

- [ ] **Step 4: Make the fetchers return lists**

In `app/core/schedule/overtime.py`, replace `get_overtime_shift_for_date` with:

```python
def get_overtime_rows_for_date(session, user_id: int, date: datetime.date) -> list:
    """Every overtime and extra-time row for a user and date, ordered by id."""
    if not session:
        return []

    from app.database.database import OvertimeShift

    return (
        session.query(OvertimeShift)
        .filter(OvertimeShift.user_id == user_id, OvertimeShift.date == date)
        .order_by(OvertimeShift.id)
        .all()
    )


def get_overtime_shift_for_date(session, user_id: int, date: datetime.date):
    """The day's primary overtime row, for callers that still want one.

    Prefers the called-in row, then any overtime row. Kept because
    app/core/schedule/__init__.py exports it and api_v1.py imports it.
    """
    rows = [r for r in get_overtime_rows_for_date(session, user_id, date) if r.kind == "ot"]
    return next((r for r in rows if r.side == "full"), rows[0] if rows else None)
```

Add `get_overtime_rows_for_date` to the imports and `__all__` in `app/core/schedule/__init__.py`, beside the existing `get_overtime_shift_for_date` entries at lines 37 and 103.

In `period.py`, `_batch_fetch_ot_shifts` currently builds `{(person, date): row}`. Change its accumulation to `setdefault((person, date), []).append(row)`, and change `_lookup_ot_shift` to return `[]` rather than `None` when nothing is found, calling `get_overtime_rows_for_date` in the session fallback.

- [ ] **Step 5: Rewrite the overlay and the callers**

`_apply_ot_display_shift` takes the row list and acts only on a `side == "full"` row:

```python
def _apply_ot_display_shift(ot_rows, date: datetime.date, shift, segments, shift_types):
    """Replace the day's worked time with OT for display, for a called-in shift only.

    Overtime that sits before or after a shift leaves the shift alone; it is
    reported through ot_hours and ot_pay, never through the segment list.
    """
    called_in = next((r for r in ot_rows if r.kind == "ot" and r.side == "full"), None)
    if called_in is None:
        return shift, segments
    ot_shift_type = next((s for s in shift_types if s.code == "OT"), None)
    if not ot_shift_type:
        return shift, segments
    try:
        ot_start, ot_end = parse_ot_times(called_in, date)
    except ValueError:
        ot_start, ot_end = None, None
    return ot_shift_type, [DaySegment(ot_start, ot_end, called_in.hours, ob_eligible=False)]
```

In both day builders, append the extra segments and recompute OB. **The position
is exact and the three neighbours are all load-bearing:**

1. after `_apply_oncall_override`, which clears both segments and `ob` for an
   overridden day, so appending earlier would resurrect hours it just removed;
2. before `_apply_ot_display_shift`, which replaces the list for a called-in
   shift and must win over the extra rows;
3. before the `hours`/`start`/`end` derivation branch 0 added at the end.

```python
    ot_rows, ot_shift_for_oncall = _lookup_ot_shifts(person_id, current_day, ot_shift_map, session)

    extra = extra_segments(ot_rows, current_day)
    if extra:
        segments = segments + extra
        ob = segment_ob(segments, combined_ob_rules)
```

Guarding on `if extra` matters: an on-call day has `ob == {}` and no segments,
and recomputing unconditionally would leave it `{}` anyway, but a day whose OB
was cleared by an override must not get a fresh all-zeros dict in place of the
empty one. Branch 0 pinned that distinction.

In `_build_person_day_basic` the same block applies with `date` for `current_day`
and `[]` for the rules, so its `_ob` stays empty as it does today.

- [ ] **Step 6: Make `_compute_overtime_pay` sum the overtime rows**

It takes one row today. It now takes the list, sums `hours` and pay across every `kind == "ot"` row, and builds `ot_details` from the `side == "full"` row if there is one, otherwise the `side == "after"` row, otherwise the first. `is_extension` in that dict becomes `row.side != "full"`.

The same derivation is needed in `app/core/schedule/overtime.py:85` (`build_ot_details`) and `app/routes/api_v1.py:183`. In `api_v1.py:57` and `api_v1.py:152`, replace the `is_extension.is_(False)` filter and the `not overtime.is_extension` check with `side == "full"`.

In `app/routes/substitutes.py:327`, replace `is_extension=False` with `kind="ot", side="full"`.

- [ ] **Step 7: Run the new tests**

Run: `venv/bin/python3 -m pytest tests/test_day_segment_kinds.py -q`
Expected: 6 passed

- [ ] **Step 8: Run the characterization suite**

Run: `venv/bin/python3 -m pytest tests/test_period_characterization.py tests/test_summary_characterization.py tests/test_day_builder_agreement.py tests/test_api_v1_characterization.py -q`

Expected: 72 passed. Unlike branch 0, these files MAY need editing here, because branch 1a changes the database shape they seed. Edit only fixture setup (an `OvertimeShift(...)` constructor that passed `is_extension` now passes `kind`/`side`). If an assertion about a computed value needs changing, stop: that is a behaviour change nobody asked for.

- [ ] **Step 9: Full suite, lint, commit**

```bash
venv/bin/python3 -m pytest -q 2>&1 | tail -3
venv/bin/python3 -m ruff check . && venv/bin/python3 -m ruff format --check .
git add -A
git commit -m "$(cat <<'MSG'
feat(schedule): read every overtime row for a day, and price extra time

A day's overtime rows are now fetched as a list. Overtime before and after the
same shift both pay, and kind="extra" rows join the segment list so they add
worked hours and earn OB on their own interval.

Extra time needs no wage_type branch: summary.py already prices
total_hours - ot_hours - substitute_hours at the hourly rate for HOURLY users,
and MONTHLY gross is the fixed salary, so both are correct by construction.

Overtime rows stay out of the segment list on purpose. day_worked_hours adds
day["hours"] and day["ot_hours"], so a row in both would report an 8.5 hour
shift with 2 hours of overtime as 12.5 hours.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MgsRgKYs94sawwQ12fmxMC
MSG
)"
```

---

### Task 4: The custom `ETC` shift

**Files:**
- Modify: `app/core/schedule/period.py:1744-1750` (the shift-override branch of `_resolve_effective_shift`)
- Modify: `app/routes/shift_override.py:17` (`_ALLOWED_CODES`) and its `add` handler
- Test: `tests/test_custom_shift_override.py`

**Interfaces:**
- Consumes: `ShiftOverride.start_time`, `end_time`, `label` from Task 1.
- Produces: `_synthetic_shift(override)` in `period.py`, returning an object with `code`, `label`, `start_time`, `end_time` and `color` for an `ETC` override, or `None` when the override is not `ETC`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_custom_shift_override.py`. Same fixture shape as Task 3, with
its own database URL and a `ShiftOverride` instead of overtime rows:

```python
"""A shift override with code ETC renders a custom block with its own times."""

import datetime
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))

# ruff: noqa: E402
import app.database.database as db_module
from app.core.schedule import clear_schedule_cache
from app.core.schedule.period import generate_month_data
from app.database.database import (
    Base,
    RotationEra,
    ShiftOverride,
    User,
    UserRole,
    WageType,
)

TEST_DB_URL = "sqlite:///file:test_custom_override_memdb?mode=memory&cache=shared&uri=true"
test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False, "uri": True})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

ERA_PATTERN = {
    "1": ["OFF", "OFF", "OFF", "N3", "N3", "N3", "N3"],
    "2": ["OFF", "OC", "N3", "N3", "N3", "N3", "OFF"],
    "3": ["OFF", "OFF", "N1", "N1", "N1", "N1", "OC"],
    "4": ["OC", "OFF", "N2", "N2", "N2", "OFF", "N1"],
    "5": ["N1", "N1", "N1", "N1", "OC", "OFF", "OFF"],
    "6": ["N3", "N3", "N3", "OFF", "OFF", "OC", "N3"],
    "7": ["N3", "N3", "OFF", "OC", "N2", "N2", "N2"],
    "8": ["N2", "N2", "OFF", "OFF", "N1", "N1", "N1"],
    "9": ["N1", "N1", "OC", "OFF", "OFF", "N2", "N2"],
    "10": ["N2", "N2", "N2", "N2", "OFF", "OFF", "OFF"],
}

TARGET = datetime.date(2026, 3, 2)


@pytest.fixture
def ovr_session(monkeypatch):
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)
    clear_schedule_cache()

    session = TestSessionLocal()
    session.query(RotationEra).delete()
    session.add(
        RotationEra(
            start_date=datetime.date(2026, 1, 2),
            end_date=None,
            rotation_length=10,
            weeks_pattern=ERA_PATTERN,
        )
    )
    session.add(
        User(
            id=1,
            username="ovruser",
            password_hash="x",
            name="Override",
            role=UserRole.USER,
            wage=30000,
            wage_type=WageType.MONTHLY,
            person_id=1,
            tax_table="33",
            vacation={},
            must_change_password=0,
        )
    )
    session.commit()

    yield session

    session.close()
    clear_schedule_cache()
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def day_for_override(ovr_session):
    def build(shift_code, start=None, end=None, label=None):
        ovr_session.add(
            ShiftOverride(
                user_id=1,
                date=TARGET,
                shift_code=shift_code,
                start_time=datetime.time.fromisoformat(start) if start else None,
                end_time=datetime.time.fromisoformat(end) if end else None,
                label=label,
            )
        )
        ovr_session.commit()
        clear_schedule_cache()
        days = generate_month_data(TARGET.year, TARGET.month, 1, session=ovr_session)
        return next(d for d in days if d["date"] == TARGET)

    return build


def test_etc_override_uses_its_own_times_and_label(day_for_override):
    day = day_for_override(shift_code="ETC", start="09:00", end="12:00", label="Kurs")
    assert day["shift"].code == "ETC"
    assert day["shift"].label == "Kurs"
    assert day["hours"] == 3.0


def test_etc_override_earns_ob_on_its_own_interval(day_for_override):
    """An evening ETC block earns OB the way a normal shift would."""
    day = day_for_override(shift_code="ETC", start="18:00", end="22:00", label="Moete")
    assert sum(day["ob"].values()) > 0


def test_etc_without_times_falls_back_rather_than_crashing(day_for_override):
    """_synthetic_shift returns None without both times, so the code lookup runs
    and finds nothing. The day must resolve to no shift, not raise."""
    day = day_for_override(shift_code="ETC")
    assert day["hours"] == 0.0


def test_a_plain_n2_override_is_unaffected(day_for_override):
    day = day_for_override(shift_code="N2")
    assert day["shift"].code == "N2"
    assert day["hours"] == 8.5
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_custom_shift_override.py -q`
Expected: FAIL, the `ETC` override resolves to no shift because no shift type carries that code.

- [ ] **Step 3: Build the synthetic shift**

In `period.py`, above `_resolve_effective_shift`:

```python
@dataclass(frozen=True)
class _SyntheticShift:
    """A shift built from a custom override rather than shift_types.json.

    Carries the same attributes the templates and calculators read off a real
    ShiftType, so nothing downstream needs to know the difference.
    """

    code: str
    label: str
    start_time: str
    end_time: str
    color: str = "#78909c"


def _synthetic_shift(override):
    """The shift an ETC override describes, or None for a plain code override."""
    if override.shift_code != "ETC" or not override.start_time or not override.end_time:
        return None
    return _SyntheticShift(
        code="ETC",
        label=override.label or "Ovrigt",
        start_time=override.start_time.strftime("%H:%M"),
        end_time=override.end_time.strftime("%H:%M"),
    )
```

`dataclass` is already imported at `period.py:5`.

In the shift-override branch of `_resolve_effective_shift`, try the synthetic shift before the lookup in `shift_types`:

```python
    if shift_override_map is not None and shift_override_map.get((person_id, current_day)):
        override = shift_override_map[(person_id, current_day)]
        result = determine_shift_for_date(current_day, person_id)
        rotation_week = result[1] if result else None
        override_shift = _synthetic_shift(override) or next(
            (s for s in shift_types if s.code == override.shift_code), None
        )
        return _with_ob(override_shift, rotation_week if override_shift else None)
```

`calculate_shift_hours` takes a shift code and reads `shift_types`, so it cannot price `ETC`. `_with_ob` must compute hours from the synthetic shift's own times instead. Extend `_with_ob` to check for `_SyntheticShift` and build its segment directly from `start_time`/`end_time`, crossing midnight when `end <= start`, the same rule `parse_ot_times` uses.

- [ ] **Step 4: Open the route**

In `app/routes/shift_override.py`, add `"ETC"` to `_ALLOWED_CODES`, accept `start_time`, `end_time` and `label` as optional form fields, and reject an `ETC` post that arrives without both times with a 400, since a custom shift with no clock times has no hours and would render as a blank row.

- [ ] **Step 5: Run the tests**

Run: `venv/bin/python3 -m pytest tests/test_custom_shift_override.py tests/test_day_segment_kinds.py -q`
Expected: 11 passed (4 override tests plus the 7 from Task 3)

- [ ] **Step 6: Full suite, lint, commit**

```bash
venv/bin/python3 -m pytest -q 2>&1 | tail -3
venv/bin/python3 -m ruff check . && venv/bin/python3 -m ruff format --check .
git add -A
git commit -m "$(cat <<'MSG'
feat(schedule): add the ETC custom shift override

A shift override with code ETC carries its own start, end and label, so a day
can show a custom block ("Kurs", "Moete") with real clock times. OB falls out of
the normal resolver path, so no new pay code was needed.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MgsRgKYs94sawwQ12fmxMC
MSG
)"
```

---

### Task 5: The overtime route accepts kind and side

**Files:**
- Modify: `app/routes/overtime.py:26-105` (`add_overtime_shift`)
- Test: `tests/test_overtime_upsert.py` (extend)

**Interfaces:**
- Consumes: the unique indexes from Task 2.
- Produces: `/overtime/add` accepting `kind` (default `"ot"`) and `side` (default `"full"`), upserting per `(user_id, date, kind, side)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_overtime_upsert.py`, following the file's existing client and fixture style:

```python
def test_two_sides_on_one_day_coexist(client, session, user):
    for side, start, end in (("before", "05:00", "06:00"), ("after", "22:30", "00:30")):
        client.post(
            "/overtime/add",
            data={"user_id": user.id, "date": "2026-06-01", "start_time": start,
                  "end_time": end, "hours": 1.0, "kind": "ot", "side": side},
        )
    rows = session.query(OvertimeShift).filter_by(user_id=user.id).all()
    assert {r.side for r in rows} == {"before", "after"}


def test_posting_the_same_side_twice_updates_rather_than_duplicates(client, session, user):
    for hours in (1.0, 3.0):
        client.post(
            "/overtime/add",
            data={"user_id": user.id, "date": "2026-06-01", "start_time": "05:00",
                  "end_time": "08:00", "hours": hours, "kind": "ot", "side": "before"},
        )
    rows = session.query(OvertimeShift).filter_by(user_id=user.id).all()
    assert len(rows) == 1
    assert rows[0].hours == 3.0


def test_extra_time_is_stored_with_zero_ot_pay(client, session, user):
    client.post(
        "/overtime/add",
        data={"user_id": user.id, "date": "2026-06-01", "start_time": "13:00",
              "end_time": "14:00", "hours": 1.0, "kind": "extra", "side": "before"},
    )
    row = session.query(OvertimeShift).filter_by(user_id=user.id).one()
    assert row.kind == "extra"
    assert row.ot_pay == 0.0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python3 -m pytest tests/test_overtime_upsert.py -q`
Expected: FAIL. The current handler deletes every row but the first, so the two sides cannot coexist.

- [ ] **Step 3: Rewrite the handler**

Replace the `is_extension: bool = Form(False)` parameter with `kind: str = Form("ot")` and `side: str = Form("full")`. Reject anything outside `{"ot", "extra"}` and `{"before", "after", "full"}` with a 400: these reach a unique index and a pay branch, so they are a trust boundary.

Compute `ot_pay` only for `kind == "ot"`; an `extra` row stores `0.0`, the convention substitutes already use.

Replace the existing-row query and the duplicate deletion with a lookup on `(user_id, date, kind, side)`, updating that row when it exists and inserting when it does not. The duplicate deletion goes: the unique indexes from Task 2 make it unreachable.

- [ ] **Step 4: Run the tests**

Run: `venv/bin/python3 -m pytest tests/test_overtime_upsert.py -q`
Expected: all passed, the file's existing tests included.

- [ ] **Step 5: Full suite, lint, commit**

```bash
venv/bin/python3 -m pytest -q 2>&1 | tail -3
venv/bin/python3 -m ruff check . && venv/bin/python3 -m ruff format --check .
git add -A
git commit -m "$(cat <<'MSG'
feat(overtime): accept kind and side, upsert per side

/overtime/add now writes one row per (user, date, kind, side), so overtime
before and after the same shift coexist and extra time can be recorded at all.
The duplicate deletion is gone: the unique indexes make it unreachable.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MgsRgKYs94sawwQ12fmxMC
MSG
)"
```

---

### Task 6: Verify against the running instance

**Files:**
- Modify: none.

- [ ] **Step 1: Confirm the pay arithmetic by hand**

```bash
venv/bin/python3 - <<'EOF'
from app.core.schedule.summary import day_worked_hours


class S:
    code = "N2"


# 8.5 h shift, 1 h extra time before it, 2 h overtime after it.
day = {"shift": S(), "hours": 9.5, "ot_hours": 2.0}
assert day_worked_hours(day) == 11.5, day_worked_hours(day)
print("worked hours:", day_worked_hours(day))
EOF
```

Expected: `worked hours: 11.5`. That is 8.5 scheduled, plus 1 extra, plus 2 overtime, each counted once.

- [ ] **Step 2: Run the migration against a copy of the dev database**

```bash
cp app/database/schedule.db /tmp/claude-1000/-home-kakan-workspace-Periodical-dev/526e5643-be84-4348-a705-99a881369283/scratchpad/migrate_test.db
DATABASE_URL="sqlite:////tmp/claude-1000/-home-kakan-workspace-Periodical-dev/526e5643-be84-4348-a705-99a881369283/scratchpad/migrate_test.db" \
  venv/bin/python3 migrations/migrate_schema.py
DATABASE_URL="sqlite:////tmp/claude-1000/-home-kakan-workspace-Periodical-dev/526e5643-be84-4348-a705-99a881369283/scratchpad/migrate_test.db" \
  venv/bin/python3 migrations/migrate_ot_kind_side.py
DATABASE_URL="sqlite:////tmp/claude-1000/-home-kakan-workspace-Periodical-dev/526e5643-be84-4348-a705-99a881369283/scratchpad/migrate_test.db" \
  venv/bin/python3 migrations/migrate_ot_kind_side.py
```

Expected: the first run reports rows backfilled and `is_extension dropped: True`; the second reports zeros throughout. Run both against the copy, never against `app/database/schedule.db`, until the counts look right.

- [ ] **Step 3: Migrate the dev database and reload**

```bash
sqlite3 app/database/schedule.db ".backup app/database/schedule.db.bak"
venv/bin/python3 migrations/migrate_schema.py
venv/bin/python3 migrations/migrate_ot_kind_side.py
```

The dev container watches `app/`, not `app/database/`, so touch a Python file or restart it to pick the schema up: `docker restart periodical_dev`.

- [ ] **Step 4: Confirm the app still renders**

Re-run the page capture used at the end of branch 0 and confirm every page returns 200 and no traceback appears:

```bash
docker logs periodical_dev --since 2m 2>&1 | grep -c Traceback
```

Expected: `0`. The rendered output will NOT match branch 0 byte for byte once a row carries a new kind, which is the point; what matters here is that nothing 500s.

- [ ] **Step 5: Report**

State the full suite count, the migration's two run outputs, and the traceback count. Branch 1b builds the UI on top of this.

---

## What this branch does not do

No UI. Nothing on the day page changes, and there is no way to create extra time or a custom shift from a browser until branch 1b ships the edit panel. The routes accept the new fields, so `curl` and the tests can exercise everything here.

The routes still take a single `date` and no `return_to`. Those change in branch 1b, together with the conflict rules.
