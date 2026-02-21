#!/usr/bin/env python3
"""Migration script to create employment_transitions table."""

import sqlite3
import sys
from pathlib import Path


def migrate(db_path: str = "app/database/schedule.db"):
    """Create employment_transitions table and ensure all columns exist."""
    path = Path(db_path)
    if not path.exists():
        print(f"Error: Database not found at {path}")
        sys.exit(1)

    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='employment_transitions'")
        if not cursor.fetchone():
            print("Creating employment_transitions table...")
            cursor.execute("""
                CREATE TABLE employment_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    transition_date DATE NOT NULL,
                    consultant_salary_type VARCHAR(10) NOT NULL,
                    consultant_vacation_days REAL NOT NULL DEFAULT 0.0,
                    consultant_supplement_pct REAL NOT NULL DEFAULT 0.0043,
                    variable_avg_daily_override REAL,
                    earning_year_start DATE,
                    earning_year_end DATE,
                    notes TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_employment_transitions_user_id UNIQUE (user_id)
                )
            """)
            cursor.execute("CREATE INDEX idx_employment_transitions_user ON employment_transitions(user_id)")
            conn.commit()
            print("Successfully created employment_transitions table.")
        else:
            print("Table 'employment_transitions' already exists.")

        # Add advance_vacation_days column if missing (added after initial migration)
        cursor.execute("PRAGMA table_info(employment_transitions)")
        columns = [row[1] for row in cursor.fetchall()]
        if "advance_vacation_days" not in columns:
            print("Adding column advance_vacation_days...")
            cursor.execute("ALTER TABLE employment_transitions ADD COLUMN advance_vacation_days INTEGER")
            conn.commit()
            print("Column advance_vacation_days added.")
        else:
            print("Column advance_vacation_days already exists.")

    except sqlite3.Error as e:
        print(f"Error during migration: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Migration: Create employment_transitions table")
    print("=" * 60)
    db = sys.argv[1] if len(sys.argv) > 1 else "app/database/schedule.db"
    migrate(db)
    print("\nMigration completed successfully!")
