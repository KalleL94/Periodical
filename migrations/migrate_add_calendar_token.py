#!/usr/bin/env python3
"""Migration script to add calendar token columns to users table."""

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
        cursor.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in cursor.fetchall()]

        if "calendar_token" not in columns:
            print("Adding column calendar_token to users...")
            cursor.execute("ALTER TABLE users ADD COLUMN calendar_token TEXT")
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_calendar_token "
                "ON users (calendar_token) WHERE calendar_token IS NOT NULL"
            )
        else:
            print("Column calendar_token already exists.")

        if "calendar_token_encrypted" not in columns:
            print("Adding column calendar_token_encrypted to users...")
            cursor.execute("ALTER TABLE users ADD COLUMN calendar_token_encrypted TEXT")
        else:
            print("Column calendar_token_encrypted already exists.")

        conn.commit()

    except sqlite3.Error as e:
        print(f"Error during migration: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Migration: Add calendar token columns to users table")
    print("=" * 60)
    db = sys.argv[1] if len(sys.argv) > 1 else "app/database/schedule.db"
    migrate(db)
    print("\nMigration completed successfully!")
