#!/usr/bin/env python3
"""Migration script to create shift_swaps table."""

import sqlite3
import sys
from pathlib import Path


def migrate(db_path: str = "app/database/schedule.db"):
    """Create shift_swaps table."""
    path = Path(db_path)
    if not path.exists():
        print(f"Error: Database not found at {path}")
        sys.exit(1)

    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shift_swaps'")
        if cursor.fetchone():
            print("Table 'shift_swaps' already exists. Skipping.")
            return

        print("Creating shift_swaps table...")
        cursor.execute("""
            CREATE TABLE shift_swaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requester_id INTEGER NOT NULL REFERENCES users(id),
                target_id INTEGER NOT NULL REFERENCES users(id),
                requester_date DATE NOT NULL,
                target_date DATE NOT NULL,
                requester_shift_code VARCHAR(10),
                target_shift_code VARCHAR(10),
                status VARCHAR(10) NOT NULL DEFAULT 'PENDING',
                message VARCHAR(255),
                responded_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("CREATE INDEX idx_shift_swaps_requester ON shift_swaps(requester_id, status)")
        cursor.execute("CREATE INDEX idx_shift_swaps_target ON shift_swaps(target_id, status)")
        cursor.execute("CREATE INDEX idx_shift_swaps_req_date ON shift_swaps(requester_date)")
        cursor.execute("CREATE INDEX idx_shift_swaps_tgt_date ON shift_swaps(target_date)")

        conn.commit()
        print("Successfully created shift_swaps table with indexes.")

    except sqlite3.Error as e:
        print(f"Error during migration: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Migration: Create shift_swaps table")
    print("=" * 60)
    migrate()
    print("\nMigration completed successfully!")
