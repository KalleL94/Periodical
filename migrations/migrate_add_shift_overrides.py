#!/usr/bin/env python3
"""
Migration: Add shift_overrides table.

Enables manual shift assignments (N1/N2/N3) that override the rotation
for a given day, displayed as regular shifts (not overtime).

Run: python migrations/migrate_add_shift_overrides.py
"""

import sqlite3
import sys

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "app/database/schedule.db"

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("""
    CREATE TABLE IF NOT EXISTS shift_overrides (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id),
        date       DATE    NOT NULL,
        shift_code TEXT    NOT NULL,
        created_at DATETIME DEFAULT (datetime('now')),
        created_by INTEGER REFERENCES users(id)
    )
""")

conn.commit()
conn.close()
print("Done: shift_overrides table created.")
