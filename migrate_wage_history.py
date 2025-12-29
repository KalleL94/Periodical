#!/usr/bin/env python3
"""
Migration script to add wage_history table and migrate existing wages.

This script:
1. Creates the wage_history table
2. Migrates existing user wages to wage_history with effective_from = earliest date in system
3. Keeps the User.wage column for backwards compatibility (will show current wage)

Usage:
    python migrate_wage_history.py
"""

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.schedule.core import get_rotation_start_date
from app.database.database import Base, User, WageHistory

# Database setup
DATABASE_URL = "sqlite:///./app/database/schedule.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def migrate():
    """Run the migration."""
    print("🚀 Starting wage history migration...")

    # Create the wage_history table
    print("\n1️⃣ Creating wage_history table...")
    Base.metadata.create_all(bind=engine, tables=[WageHistory.__table__])
    print("   ✅ Table created")

    # Get rotation start date as the effective_from date for all existing wages
    rotation_start = get_rotation_start_date()
    print(f"\n2️⃣ Using rotation start date as effective_from: {rotation_start}")

    # Migrate existing wages
    session = SessionLocal()
    try:
        users = session.query(User).all()
        print(f"\n3️⃣ Migrating wages for {len(users)} users...")

        migrated_count = 0
        for user in users:
            # Check if wage history already exists for this user
            existing = (
                session.query(WageHistory)
                .filter(WageHistory.user_id == user.id, WageHistory.effective_to.is_(None))
                .first()
            )

            if existing:
                print(f"   ⚠️  User {user.name} (ID: {user.id}) already has wage history, skipping")
                continue

            # Create wage history entry
            wage_history = WageHistory(
                user_id=user.id,
                wage=user.wage,
                effective_from=rotation_start,
                effective_to=None,  # NULL = current wage
                created_at=datetime.utcnow(),
                created_by=None,  # System migration
            )

            session.add(wage_history)
            migrated_count += 1
            print(f"   ✅ Migrated {user.name} (ID: {user.id}): {user.wage} SEK from {rotation_start}")

        session.commit()
        print("\n4️⃣ Migration complete!")
        print(f"   📊 Migrated {migrated_count} user wages")
        print(f"   📊 Skipped {len(users) - migrated_count} users (already had history)")

    except Exception as e:
        session.rollback()
        print(f"\n❌ Migration failed: {e}")
        raise
    finally:
        session.close()

    print("\n✅ Wage history migration completed successfully!")
    print("\n💡 Next steps:")
    print("   1. Test the wage history queries")
    print("   2. Update admin UI to manage wage history")
    print("   3. User.wage column is kept for backwards compatibility")


if __name__ == "__main__":
    migrate()
