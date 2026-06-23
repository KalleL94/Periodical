#!/usr/bin/env python3
"""
Migration: allow overtime, absence and on-call rows to reference a substitute.

Adds a nullable ``substitute_id`` column to ``overtime_shifts``, ``absences`` and
``oncall_overrides`` and makes their ``user_id`` column nullable. SQLite cannot drop a
NOT NULL constraint in place, so each table is rebuilt (create new, copy data, drop,
rename). Exactly one of user_id / substitute_id is expected to be set on each row; this
is enforced at the route layer, not by a DB constraint.

The script is idempotent: a table that already has a substitute_id column is skipped.

Usage:
    python migrations/migrate_substitute_ot_absence.py
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ruff: noqa: E402
from sqlalchemy import text

from app.database.database import engine

# Target schema for each rebuilt table. user_id is nullable and substitute_id is added.
NEW_TABLES = {
    "overtime_shifts": """
        CREATE TABLE overtime_shifts_new (
            id INTEGER NOT NULL PRIMARY KEY,
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
            FOREIGN KEY(user_id) REFERENCES users (id),
            FOREIGN KEY(substitute_id) REFERENCES substitutes (id),
            FOREIGN KEY(created_by) REFERENCES users (id)
        )
    """,
    "absences": """
        CREATE TABLE absences_new (
            id INTEGER NOT NULL PRIMARY KEY,
            user_id INTEGER,
            substitute_id INTEGER,
            date DATE NOT NULL,
            absence_type VARCHAR NOT NULL,
            left_at VARCHAR(5),
            arrived_at VARCHAR(5),
            created_at DATETIME,
            FOREIGN KEY(user_id) REFERENCES users (id),
            FOREIGN KEY(substitute_id) REFERENCES substitutes (id)
        )
    """,
    "oncall_overrides": """
        CREATE TABLE oncall_overrides_new (
            id INTEGER NOT NULL PRIMARY KEY,
            user_id INTEGER,
            substitute_id INTEGER,
            date DATE NOT NULL,
            override_type VARCHAR NOT NULL,
            reason VARCHAR(255),
            created_at DATETIME,
            created_by INTEGER,
            FOREIGN KEY(user_id) REFERENCES users (id),
            FOREIGN KEY(substitute_id) REFERENCES substitutes (id),
            FOREIGN KEY(created_by) REFERENCES users (id)
        )
    """,
}


def _existing_columns(conn, table: str) -> list[str]:
    result = conn.execute(text(f"PRAGMA table_info({table})"))
    return [row[1] for row in result]


def _rebuild_table(table: str, create_sql: str) -> None:
    """Rebuild a single table, copying the intersection of old and new columns."""
    with engine.connect() as conn:
        columns = _existing_columns(conn, table)

    if not columns:
        print(f"   Skipping {table}: table not found.")
        return
    if "substitute_id" in columns:
        print(f"   Skipping {table}: substitute_id already present.")
        return

    # New table columns (excluding the freshly added substitute_id which has no source data)
    new_table = f"{table}_new"

    # Toggle foreign keys outside the transaction (SQLite requirement for table rebuilds).
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.commit()

    try:
        with engine.begin() as conn:
            conn.execute(text(create_sql))
            new_columns = _existing_columns(conn, new_table)
            # Copy only columns present in both old and new (substitute_id stays NULL).
            shared = [c for c in new_columns if c in columns]
            col_list = ", ".join(shared)
            conn.execute(text(f"INSERT INTO {new_table} ({col_list}) SELECT {col_list} FROM {table}"))
            conn.execute(text(f"DROP TABLE {table}"))
            conn.execute(text(f"ALTER TABLE {new_table} RENAME TO {table}"))
        print(f"   Rebuilt {table}: added substitute_id, user_id now nullable.")
    finally:
        with engine.connect() as conn:
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.commit()


def migrate() -> None:
    print("Starting substitute OT/absence/on-call migration...")
    for table, create_sql in NEW_TABLES.items():
        _rebuild_table(table, create_sql)
    print("Migration completed.")


if __name__ == "__main__":
    migrate()
