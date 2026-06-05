"""Migration: add arrived_at column to absences table."""

import sqlite3
import sys

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "app/database/schedule.db"

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("PRAGMA table_info(absences)")
columns = [row[1] for row in cur.fetchall()]

if "arrived_at" not in columns:
    cur.execute("ALTER TABLE absences ADD COLUMN arrived_at VARCHAR(5)")
    conn.commit()
    print("Added arrived_at column.")
else:
    print("Column already exists, skipping.")

conn.close()
