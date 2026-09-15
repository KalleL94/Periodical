"""Checks for the one-off data migration that backfills kind/side and drops is_extension."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

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
    with pytest.raises(IntegrityError):
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
    with pytest.raises(IntegrityError):
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
