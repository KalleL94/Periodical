#!/usr/bin/env python3
"""
Migration script to add the counts_as_vacation_day column to absences.

VACATION absences default to counting as a vacation day, so every existing row
is backfilled to True and the schedule behaves exactly as before until a day is
explicitly excluded.

Usage:
    python migrations/migrate_absence_counts_as_vacation.py
"""

from sqlalchemy import create_engine, inspect, text

DATABASE_URL = "sqlite:///./app/database/schedule.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def migrate():
    """Run the migration."""
    print("Starting counts_as_vacation_day migration...")

    columns = {c["name"] for c in inspect(engine).get_columns("absences")}
    if "counts_as_vacation_day" in columns:
        print("   Column counts_as_vacation_day already exists, nothing to do")
        return

    with engine.begin() as conn:
        # SQLite adds the column with the given default; existing rows get 1 (True).
        conn.execute(text("ALTER TABLE absences ADD COLUMN counts_as_vacation_day BOOLEAN NOT NULL DEFAULT 1"))
    print("   Added column counts_as_vacation_day (default True)")
    print("Migration complete")


if __name__ == "__main__":
    migrate()
