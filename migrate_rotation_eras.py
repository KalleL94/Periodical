#!/usr/bin/env python3
"""
Migration script: Initialize rotation_eras table with existing rotation configuration.

This script creates the first rotation era based on the current rotation.json and settings.json files.
This ensures historical schedule calculations remain accurate when rotation length changes in the future.

Usage:
    python migrate_rotation_eras.py

This will:
1. Read current rotation.json and settings.json
2. Create the rotation_eras table if it doesn't exist
3. Insert the initial era with current rotation configuration
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.database.database import Base, RotationEra, SessionLocal, engine


def load_rotation_json():
    """Load rotation configuration from JSON file."""
    rotation_path = Path("data/rotation.json")
    if not rotation_path.exists():
        raise FileNotFoundError(f"Could not find {rotation_path}")

    with open(rotation_path, encoding="utf-8") as f:
        return json.load(f)


def load_settings_json():
    """Load settings from JSON file."""
    settings_path = Path("data/settings.json")
    if not settings_path.exists():
        raise FileNotFoundError(f"Could not find {settings_path}")

    with open(settings_path, encoding="utf-8") as f:
        return json.load(f)


def migrate():
    """Run the migration."""
    print("\n" + "=" * 70)
    print("MIGRATION: Initialize rotation_eras table")
    print("=" * 70 + "\n")

    # Load current configuration
    print("1. Loading current rotation configuration...")
    try:
        rotation = load_rotation_json()
        settings = load_settings_json()
        print(f"   [OK] Rotation length: {rotation['rotation_length']} weeks")
        print(f"   [OK] Start date: {settings['rotation_start_date']}")
    except FileNotFoundError as e:
        print(f"   [ERROR] {e}")
        return False

    # Create tables (if they don't exist)
    print("\n2. Creating/verifying database tables...")
    Base.metadata.create_all(bind=engine)
    print("   [OK] Tables ready")

    # Create database session
    db = SessionLocal()

    try:
        # Check if rotation_eras table already has data
        existing_count = db.query(RotationEra).count()
        if existing_count > 0:
            print(f"\n   [WARNING] rotation_eras table already has {existing_count} era(s).")
            response = input("   Delete existing eras and re-import? (y/N): ")
            if response.lower() != "y":
                print("   Migration cancelled.")
                return False
            db.query(RotationEra).delete()
            db.commit()
            print("   [OK] Existing eras deleted")

        # Create initial era
        print("\n3. Creating initial rotation era...")

        initial_era = RotationEra(
            start_date=datetime.strptime(settings["rotation_start_date"], "%Y-%m-%d").date(),
            end_date=None,  # NULL = ongoing/current era
            rotation_length=rotation["rotation_length"],
            weeks_pattern=rotation["weeks"],
            created_by=None,  # System migration, no user
        )

        db.add(initial_era)
        db.commit()

        print("   [OK] Created initial era:")
        print(f"        - Start date: {initial_era.start_date}")
        print(f"        - End date: {initial_era.end_date or 'Ongoing (NULL)'}")
        print(f"        - Rotation length: {initial_era.rotation_length} weeks")
        print(f"        - Weeks pattern: {len(initial_era.weeks_pattern)} weeks defined")

        # Print summary
        print("\n" + "=" * 70)
        print("MIGRATION COMPLETE")
        print("=" * 70)
        print("The rotation_eras table has been initialized with your current rotation")
        print("configuration. You can now safely add new eras with different rotation")
        print("lengths without affecting historical schedule calculations.")
        print("=" * 70)

        return True

    except Exception as e:
        print(f"\n   [ERROR] Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
