#!/usr/bin/env python3
"""
Migration script to add absences table to existing database.

This script:
1. Creates the absences table if it doesn't exist
2. Preserves all existing data
3. Can be run multiple times safely (idempotent)

The absences table tracks any type of absence (sick leave, VAB, etc.)
without distinguishing between types.

Usage:
    python migrate_absence.py
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# ruff: noqa: E402
from sqlalchemy import inspect

from app.database.database import Base, engine


def table_exists(table_name: str) -> bool:
    """Check if a table exists in the database."""
    inspector = inspect(engine)
    return table_name in inspector.get_table_names()


def migrate():
    """Run the migration to add absences table."""
    print("🔄 Starting absence table migration...")

    # Check if table already exists
    if table_exists("absences"):
        print("✅ Absences table already exists. No migration needed.")
        return

    print("📝 Creating absences table...")
    print("   Table structure: id, user_id, date, created_at")
    print("   Note: No absence_type column - all absences are treated equally")

    try:
        # Create only the absences table
        # This will not affect existing tables
        Base.metadata.create_all(bind=engine, checkfirst=True)
        print("✅ Absences table created successfully!")

    except Exception as e:
        print(f"❌ Migration failed: {e}")
        raise

    print("🎉 Migration completed successfully!")
    print("   Users can now register absences via the day view")


if __name__ == "__main__":
    migrate()
