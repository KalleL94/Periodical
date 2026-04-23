#!/usr/bin/env python3
"""
Migration script to add vacation_saved column to users table.

Adds:
- vacation_saved: JSON column for tracking saved/carried-forward vacation days
  Format: {"2025": {"saved": 3, "paid_out": 2, "payout_amount": 3404.0}, ...}
"""

import sqlite3
import sys
from pathlib import Path


def migrate():
    """Add vacation_saved column to users table."""
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

        if "vacation_saved" not in columns:
            print("Adding vacation_saved column...")
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN vacation_saved TEXT DEFAULT '{}'
            """)
            conn.commit()
            print("Successfully added vacation_saved column.")
        else:
            print("Column 'vacation_saved' already exists. Skipping.")

        # Verify
        cursor.execute("SELECT id, username, vacation_saved FROM users")
        rows = cursor.fetchall()
        print(f"\nCurrent state ({len(rows)} users):")
        for row in rows:
            print(f"  id={row[0]}, username={row[1]}, vacation_saved={row[2]}")

    except sqlite3.Error as e:
        print(f"Error during migration: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Migration: Add vacation_saved column to users table")
    print("=" * 60)
    migrate()
    print("\nMigration completed successfully!")
