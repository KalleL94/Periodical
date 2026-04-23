#!/usr/bin/env python3
"""Migration script to add api_key column to users table."""

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

        if "api_key" not in columns:
            print("Adding column api_key to users...")
            cursor.execute("ALTER TABLE users ADD COLUMN api_key TEXT")
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_api_key ON users (api_key) WHERE api_key IS NOT NULL"
            )
            conn.commit()
            print("Column api_key added.")
        else:
            print("Column api_key already exists.")

    except sqlite3.Error as e:
        print(f"Error during migration: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Migration: Add api_key column to users table")
    print("=" * 60)
    db = sys.argv[1] if len(sys.argv) > 1 else "app/database/schedule.db"
    migrate(db)
    print("\nMigration completed successfully!")
