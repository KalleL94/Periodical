#!/usr/bin/env python3
"""
Migration: Create day_pay_overrides table.

Stores manual overrides for OB and on-call pay on a specific day per user.

Run: python migrations/migrate_day_pay_overrides.py
"""

import sqlite3
import sys

DB_PATH = "app/database/schedule.db"


def migrate(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='day_pay_overrides'")
    if cursor.fetchone():
        print("Table 'day_pay_overrides' already exists – skipping.")
        conn.close()
        return

    cursor.execute(
        """
        CREATE TABLE day_pay_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            date DATE NOT NULL,
            ob_hours_override TEXT,
            oncall_hours_override TEXT,
            reason VARCHAR(255),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER REFERENCES users(id)
        )
        """
    )
    conn.commit()
    conn.close()
    print("Migration complete: created 'day_pay_overrides' table.")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    migrate(path)
