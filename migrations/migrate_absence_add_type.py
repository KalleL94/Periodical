#!/usr/bin/env python3
"""
Migration script to add absence_type column to absences table.

This script:
1. Adds absence_type column to existing absences table
2. Sets default value 'SICK' for any existing records
3. Can be run multiple times safely (idempotent)

Usage:
    python migrate_absence_add_type.py
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text  # noqa: E402

from app.database.database import engine  # noqa: E402


def migrate():
    """Run the migration to add absence_type column."""
    print("🔄 Starting absence_type column migration...")

    # First check if migration is needed
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(absences)"))
        columns = [row[1] for row in result]

        if "absence_type" in columns:
            print("✅ absence_type column already exists. No migration needed.")
            return

    print("📝 Adding absence_type column to absences table...")
    print("   Types: SICK (Sjuk), VAB (Vård av barn), LEAVE (Ledigt)")

    # Perform migration in a new connection with transaction
    with engine.begin() as conn:
        try:
            # Check if there are any existing records
            result = conn.execute(text("SELECT COUNT(*) FROM absences"))
            count = result.scalar()

            if count > 0:
                print(f"   Found {count} existing absence records")

                # Create new table with absence_type
                conn.execute(
                    text("""
                    CREATE TABLE absences_new (
                        id INTEGER NOT NULL PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        date DATE NOT NULL,
                        absence_type VARCHAR(10) NOT NULL,
                        created_at DATETIME,
                        FOREIGN KEY(user_id) REFERENCES users (id)
                    )
                """)
                )

                # Copy data from old table, setting all to SICK by default
                conn.execute(
                    text("""
                    INSERT INTO absences_new (id, user_id, date, absence_type, created_at)
                    SELECT id, user_id, date, 'SICK', created_at
                    FROM absences
                """)
                )

                print("   Set all existing absences to type 'SICK' by default")

                # Drop old table
                conn.execute(text("DROP TABLE absences"))

                # Rename new table
                conn.execute(text("ALTER TABLE absences_new RENAME TO absences"))
            else:
                print("   No existing records, adding column directly")

                # If no records, we can just add the column
                # SQLite doesn't support adding NOT NULL columns directly, so we need to recreate
                conn.execute(
                    text("""
                    CREATE TABLE absences_new (
                        id INTEGER NOT NULL PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        date DATE NOT NULL,
                        absence_type VARCHAR(10) NOT NULL,
                        created_at DATETIME,
                        FOREIGN KEY(user_id) REFERENCES users (id)
                    )
                """)
                )

                # Drop old empty table
                conn.execute(text("DROP TABLE absences"))

                # Rename new table
                conn.execute(text("ALTER TABLE absences_new RENAME TO absences"))

            print("✅ absence_type column added successfully!")
            print("   Absence types:")
            print("   - SICK: Sjukfrånvaro (röd #ef4444)")
            print("   - VAB: Vård av barn (orange #f97316)")
            print("   - LEAVE: Ledigt/Permission (lila #a855f7)")

        except Exception as e:
            print(f"❌ Migration failed: {e}")
            raise

    print("🎉 Migration completed successfully!")
    print("   Users can now register different types of absences")


if __name__ == "__main__":
    migrate()
