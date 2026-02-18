#!/usr/bin/env python3
"""
Migration: Add is_extension column to overtime_shifts table.

This enables shift extensions (staying extra time after a regular shift)
distinct from full overtime call-in shifts.

Run: python migrate_add_ot_extension.py
"""

import sqlite3
import sys

DB_PATH = "app/database/schedule.db"


def migrate(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Check if column already exists
    cursor.execute("PRAGMA table_info(overtime_shifts)")
    columns = [row[1] for row in cursor.fetchall()]

    if "is_extension" in columns:
        print("Column 'is_extension' already exists – skipping.")
        conn.close()
        return

    cursor.execute("ALTER TABLE overtime_shifts ADD COLUMN is_extension BOOLEAN NOT NULL DEFAULT 0")
    conn.commit()
    conn.close()
    print("Migration complete: added 'is_extension' to overtime_shifts.")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    migrate(path)
