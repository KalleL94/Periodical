#!/usr/bin/env python3
"""Migration: Add custom_rates column + rate_history table.

1. users.custom_rates - JSON for current rate overrides (quick access)
2. rate_history - temporal rate tracking (like wage_history)
"""

import sqlite3
import sys
from pathlib import Path


def migrate(db_path: str = "app/database/schedule.db"):
    path = Path(db_path)
    if not path.exists():
        print(f"Error: Database not found at {path}")
        sys.exit(1)

    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    try:
        # 1. Add custom_rates column to users
        cursor.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in cursor.fetchall()]

        if "custom_rates" not in columns:
            print("Adding custom_rates column to users...")
            cursor.execute("ALTER TABLE users ADD COLUMN custom_rates TEXT DEFAULT '{}'")
            print("  Done.")
        else:
            print("Column 'custom_rates' already exists.")

        # 2. Create rate_history table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rate_history'")
        if cursor.fetchone() is None:
            print("Creating rate_history table...")
            cursor.execute("""
                CREATE TABLE rate_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    rates TEXT NOT NULL DEFAULT '{}',
                    effective_from DATE NOT NULL,
                    effective_to DATE,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    created_by INTEGER REFERENCES users(id)
                )
            """)
            cursor.execute("CREATE INDEX ix_rate_history_user_id ON rate_history(user_id)")
            cursor.execute("CREATE INDEX ix_rate_history_effective ON rate_history(user_id, effective_from)")
            print("  Done.")
        else:
            print("Table 'rate_history' already exists.")

        conn.commit()

        # Show current state
        cursor.execute("SELECT id, username, custom_rates FROM users")
        print("\nUsers:")
        for row in cursor.fetchall():
            print(f"  id={row[0]}, username={row[1]}, custom_rates={row[2]}")

        cursor.execute("SELECT COUNT(*) FROM rate_history")
        print(f"Rate history records: {cursor.fetchone()[0]}")

    except sqlite3.Error as e:
        print(f"Error: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
