#!/usr/bin/env python3
"""Migration script to add language column to users table."""

import sqlite3
import sys
from pathlib import Path


def migrate(db_path: str = "app/database/schedule.db"):
    """Add language column to users table."""
    path = Path(db_path)
    if not path.exists():
        print(f"Error: Database not found at {path}")
        sys.exit(1)

    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    try:
        cursor.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in cursor.fetchall()]

        if "language" not in columns:
            print("Adding column language to users...")
            cursor.execute("ALTER TABLE users ADD COLUMN language TEXT NOT NULL DEFAULT 'sv'")
            conn.commit()
            print("Column language added.")
        else:
            print("Column language already exists.")

    except sqlite3.Error as e:
        print(f"Error during migration: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Migration: Add language column to users table")
    print("=" * 60)
    db = sys.argv[1] if len(sys.argv) > 1 else "app/database/schedule.db"
    migrate(db)
    print("\nMigration completed successfully!")
