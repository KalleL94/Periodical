"""Migration for issue #290 (substitutes.user_id + substitutes.hourly_wage):
must add both columns to a pre-migration database and be idempotent."""

import sqlite3
import subprocess
import sys
from pathlib import Path

MIGRATION = Path(__file__).parent.parent / "migrations" / "migrate_substitute_account_link.py"


def _make_pre_migration_db(path: Path) -> None:
    """Create a minimal DB with the pre-#290 substitutes schema."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER NOT NULL PRIMARY KEY,
            username VARCHAR(50) NOT NULL,
            name VARCHAR(100) NOT NULL
        );
        CREATE TABLE substitutes (
            id INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME,
            created_by INTEGER,
            FOREIGN KEY(created_by) REFERENCES users (id)
        );
        INSERT INTO substitutes (id, name, is_active) VALUES (1, 'Sommarvikarie', 1);
        """
    )
    conn.commit()
    conn.close()


def _columns(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(substitutes)").fetchall()]
    conn.close()
    return cols


def _run_migration(path: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(MIGRATION), str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_migration_adds_columns_and_is_idempotent(tmp_path):
    db_path = tmp_path / "schedule.db"
    _make_pre_migration_db(db_path)
    assert "user_id" not in _columns(db_path)
    assert "hourly_wage" not in _columns(db_path)

    out_first = _run_migration(db_path)
    assert "user_id" in out_first and "hourly_wage" in out_first
    cols = _columns(db_path)
    assert "user_id" in cols
    assert "hourly_wage" in cols

    # Existing rows survive with NULL in the new columns
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT name, user_id, hourly_wage FROM substitutes WHERE id = 1").fetchone()
    conn.close()
    assert row == ("Sommarvikarie", None, None)

    # Second run must be a no-op
    out_second = _run_migration(db_path)
    assert "Already exists" in out_second
    assert _columns(db_path) == cols


def test_migration_fills_single_missing_column(tmp_path):
    """A DB where only user_id exists (partial earlier run) gets hourly_wage added."""
    db_path = tmp_path / "schedule.db"
    _make_pre_migration_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE substitutes ADD COLUMN user_id INTEGER REFERENCES users(id)")
    conn.commit()
    conn.close()

    out = _run_migration(db_path)
    assert "hourly_wage" in out
    cols = _columns(db_path)
    assert cols.count("user_id") == 1
    assert "hourly_wage" in cols
