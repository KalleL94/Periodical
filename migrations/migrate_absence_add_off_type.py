#!/usr/bin/env python3
"""
Migration script to add OFF absence type support.

This migration is a no-op for the database schema since AbsenceType is stored as a string.
The OFF type is already supported in the code, this script just validates the setup.

Run with: python migrate_absence_add_off_type.py
"""

import sqlite3
from pathlib import Path


def migrate():
    """Validate database is ready for OFF absence type."""
    db_path = Path("app/database/schedule.db")

    if not db_path.exists():
        print(f"❌ Database not found at {db_path}")
        print("   Run migrate_to_db.py first to create the database.")
        return False

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Check if absences table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='absences'")
        if not cursor.fetchone():
            print("❌ Absences table not found")
            print("   Run migrate_absence.py first to create the absences table.")
            return False

        # Check table structure
        cursor.execute("PRAGMA table_info(absences)")
        columns = {col[1]: col[2] for col in cursor.fetchall()}

        print("✅ Absences table found")
        print(f"   Columns: {', '.join(columns.keys())}")

        # Verify absence_type column exists
        if "absence_type" not in columns:
            print("❌ absence_type column not found")
            return False

        print("✅ absence_type column exists")

        # Check if there are any existing OFF absences
        cursor.execute("SELECT COUNT(*) FROM absences WHERE absence_type = 'OFF'")
        off_count = cursor.fetchone()[0]

        print(f"ℹ️  Current OFF absences: {off_count}")

        print("\n✅ Database is ready for OFF absence type!")
        print("\nOFF absence type features:")
        print("- Can be manually registered on any day")
        print("- Uses gray color (#888888) from shift_types.json")
        print("- 0% wage deduction (paid time off)")
        print("- Appears in year summary statistics")
        print("\nNo database changes needed - OFF type is already supported!")
        return True

    except sqlite3.Error as e:
        print(f"❌ Validation failed: {e}")
        return False

    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("OFF Absence Type Migration")
    print("=" * 60)
    print()
    migrate()
