#!/usr/bin/env python3
"""
Migration script to add vacation-related columns to users table.

Adds:
- employment_start_date: When the employee started working (for vacation balance)
- vacation_year_start_month: Month (1-12) when vacation year starts (default 4 = April)
- vacation_days_per_year: Annual vacation entitlement (default 25)

Also backfills employment_start_date from person_history where possible.
"""

import sqlite3
import sys
from pathlib import Path


def migrate():
    """Add vacation fields to users table."""
    db_path = Path("app/database/schedule.db")

    if not db_path.exists():
        print(f"Error: Database not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Check existing columns
        cursor.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in cursor.fetchall()]

        added = []

        # Add employment_start_date
        if "employment_start_date" not in columns:
            print("Adding employment_start_date column...")
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN employment_start_date DATE
            """)
            added.append("employment_start_date")
        else:
            print("Column 'employment_start_date' already exists. Skipping.")

        # Add vacation_year_start_month
        if "vacation_year_start_month" not in columns:
            print("Adding vacation_year_start_month column...")
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN vacation_year_start_month INTEGER DEFAULT 4 NOT NULL
            """)
            # Ensure all existing rows have the default
            cursor.execute("""
                UPDATE users
                SET vacation_year_start_month = 4
                WHERE vacation_year_start_month IS NULL
            """)
            added.append("vacation_year_start_month")
        else:
            print("Column 'vacation_year_start_month' already exists. Skipping.")

        # Add vacation_days_per_year
        if "vacation_days_per_year" not in columns:
            print("Adding vacation_days_per_year column...")
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN vacation_days_per_year INTEGER DEFAULT 25 NOT NULL
            """)
            # Ensure all existing rows have the default
            cursor.execute("""
                UPDATE users
                SET vacation_days_per_year = 25
                WHERE vacation_days_per_year IS NULL
            """)
            added.append("vacation_days_per_year")
        else:
            print("Column 'vacation_days_per_year' already exists. Skipping.")

        # Backfill employment_start_date from person_history
        if "employment_start_date" in added:
            print("\nBackfilling employment_start_date from person_history...")
            cursor.execute("""
                UPDATE users
                SET employment_start_date = (
                    SELECT MIN(ph.effective_from)
                    FROM person_history ph
                    WHERE ph.user_id = users.id
                )
                WHERE employment_start_date IS NULL
                AND EXISTS (
                    SELECT 1 FROM person_history ph WHERE ph.user_id = users.id
                )
            """)
            backfilled = cursor.rowcount
            print(f"  Backfilled {backfilled} user(s) from person_history")

        conn.commit()

        if added:
            print(f"\nSuccessfully added columns: {', '.join(added)}")
        else:
            print("\nNo changes needed - all columns already exist.")

        # Verify
        cursor.execute("""
            SELECT id, username, employment_start_date, vacation_year_start_month, vacation_days_per_year
            FROM users
        """)
        rows = cursor.fetchall()
        print(f"\nCurrent state ({len(rows)} users):")
        for row in rows:
            print(f"  id={row[0]}, username={row[1]}, start={row[2]}, break_month={row[3]}, days/year={row[4]}")

    except sqlite3.Error as e:
        print(f"Error during migration: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Migration: Add vacation fields to users table")
    print("=" * 60)
    migrate()
    print("\nMigration completed successfully!")
