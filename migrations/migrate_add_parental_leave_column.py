"""Migration: add parental_leave JSON column to users table."""

import sqlite3
import sys

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "app/database/schedule.db"

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("PRAGMA table_info(users)")
columns = [row[1] for row in cur.fetchall()]

if "parental_leave" not in columns:
    cur.execute("ALTER TABLE users ADD COLUMN parental_leave JSON DEFAULT '{}'")
    conn.commit()
    print("Added parental_leave column.")
else:
    print("Column already exists, skipping.")

conn.close()
