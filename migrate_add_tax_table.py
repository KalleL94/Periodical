#!/usr/bin/env python3
"""
Migration script to add tax_table column to users table.

This adds support for selecting which Swedish tax table to use for tax calculations.
Default is table 33.
"""

import sqlite3
import sys
from pathlib import Path


def migrate():
    """Add tax_table column to users table."""
    db_path = Path("app/database/schedule.db")

    if not db_path.exists():
        print(f"Error: Database not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Check if column already exists
        cursor.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in cursor.fetchall()]

        if "tax_table" in columns:
            print("Column 'tax_table' already exists in users table. Skipping migration.")
            return

        # Add tax_table column with default value "33"
        print("Adding tax_table column to users table...")
        cursor.execute("""
            ALTER TABLE users 
            ADD COLUMN tax_table VARCHAR(10) DEFAULT '33'
        """)

        # Update existing users to have tax_table = "33"
        cursor.execute("""
            UPDATE users 
            SET tax_table = '33' 
            WHERE tax_table IS NULL
        """)

        conn.commit()
        print("✓ Successfully added tax_table column")
        print("✓ Set default tax_table='33' for all existing users")

        # Verify the change
        cursor.execute("SELECT COUNT(*) FROM users WHERE tax_table = '33'")
        count = cursor.fetchone()[0]
        print(f"✓ {count} user(s) now have tax_table='33'")

    except sqlite3.Error as e:
        print(f"Error during migration: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Migration: Add tax_table column to users table")
    print("=" * 60)
    migrate()
    print("\nMigration completed successfully!")
