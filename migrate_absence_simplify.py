#!/usr/bin/env python3
"""
Migration script to simplify absences table by removing absence_type column.

This script:
1. Creates a new absences table without absence_type column
2. Copies all existing absence records (ignoring the type)
3. Drops the old table and renames the new one
4. Can be run multiple times safely (idempotent)

Usage:
    python migrate_absence_simplify.py
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# ruff: noqa: E402
from sqlalchemy import text

from app.database.database import engine


def migrate():
    """Run the migration to simplify absences table."""
    print("🔄 Starting absence table simplification migration...")

    # First check if migration is needed
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(absences)"))
        columns = [row[1] for row in result]

        if "absence_type" not in columns:
            print("✅ Absences table already simplified. No migration needed.")
            return

    print("📝 Simplifying absences table (removing absence_type column)...")

    # Perform migration in a new connection with transaction
    with engine.begin() as conn:
        try:
            # Create new table without absence_type
            conn.execute(
                text("""
                CREATE TABLE absences_new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    date DATE NOT NULL,
                    created_at DATETIME,
                    FOREIGN KEY(user_id) REFERENCES users (id)
                )
            """)
            )

            # Copy data from old table (ignoring absence_type)
            conn.execute(
                text("""
                INSERT INTO absences_new (id, user_id, date, created_at)
                SELECT id, user_id, date, created_at
                FROM absences
            """)
            )

            # Drop old table
            conn.execute(text("DROP TABLE absences"))

            # Rename new table
            conn.execute(text("ALTER TABLE absences_new RENAME TO absences"))

            print("✅ Absences table simplified successfully!")
            print("   Removed absence_type column - all absences now treated equally")

        except Exception as e:
            print(f"❌ Migration failed: {e}")
            raise

    print("🎉 Migration completed successfully!")


if __name__ == "__main__":
    migrate()
