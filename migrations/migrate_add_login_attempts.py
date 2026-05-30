#!/usr/bin/env python3
"""Migration script to add the login_attempts table (login brute-force protection).

Note: create_tables() (Base.metadata.create_all) also creates this table automatically
on startup. This script exists so the table can be created explicitly during a controlled
prod deploy. Always back up the prod DB before running migrations.
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
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='login_attempts'")
        exists = cursor.fetchone() is not None

        if exists:
            print("Table login_attempts already exists.")
            return

        print("Creating table login_attempts...")
        cursor.execute(
            """
            CREATE TABLE login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username VARCHAR(50) NOT NULL,
                ip VARCHAR(64) NOT NULL,
                created_at DATETIME NOT NULL
            )
            """
        )
        cursor.execute("CREATE INDEX ix_login_attempts_username ON login_attempts (username)")
        cursor.execute("CREATE INDEX ix_login_attempts_created_at ON login_attempts (created_at)")
        conn.commit()
        print("Table login_attempts created.")

    except sqlite3.Error as e:
        print(f"Error during migration: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Migration: Add login_attempts table")
    print("=" * 60)
    db = sys.argv[1] if len(sys.argv) > 1 else "app/database/schedule.db"
    migrate(db)
    print("\nMigration completed successfully!")
