#!/usr/bin/env python3
"""Migration: add wage_type column to users table (default 'monthly')."""

import sqlite3
import sys

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "app/database/schedule.db"

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

existing = [row[1] for row in cur.execute("PRAGMA table_info(users)").fetchall()]
if "wage_type" not in existing:
    cur.execute("ALTER TABLE users ADD COLUMN wage_type TEXT NOT NULL DEFAULT 'MONTHLY'")
    conn.commit()
    print("Done: wage_type column added.")
else:
    print("Already exists: wage_type column.")

conn.close()
