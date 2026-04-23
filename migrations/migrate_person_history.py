#!/usr/bin/env python3
"""
Migration script to add person_history table and is_active field to users.

This script:
1. Creates the person_history table
2. Adds is_active column to users table (defaults to 1 = active)
3. Backfills PersonHistory records for all current users (person_id = user_id)
4. Sets all current users as active from rotation_start_date

Usage:
    python migrate_person_history.py
"""

from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.schedule.core import get_rotation_start_date
from app.database.database import Base, PersonHistory, User

# Database setup
DATABASE_URL = "sqlite:///./app/database/schedule.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def migrate():
    """Run the migration."""
    print("🚀 Starting person history migration...")

    # Create the person_history table
    print("\n1️⃣ Creating person_history table...")
    Base.metadata.create_all(bind=engine, tables=[PersonHistory.__table__])
    print("   ✅ Table created")

    # Add new columns to users table
    print("\n2️⃣ Adding new columns to users table...")
    session = SessionLocal()
    try:
        # Check which columns already exist
        result = session.execute(text("PRAGMA table_info(users)"))
        columns = [row[1] for row in result.fetchall()]

        if "is_active" not in columns:
            session.execute(text("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1 NOT NULL"))
            session.commit()
            print("   ✅ is_active column added (default: 1 = active)")
        else:
            print("   ⚠️  is_active column already exists, skipping")

        if "person_id" not in columns:
            session.execute(text("ALTER TABLE users ADD COLUMN person_id INTEGER"))
            session.commit()
            print("   ✅ person_id column added (default: NULL)")
        else:
            print("   ⚠️  person_id column already exists, skipping")

    except Exception as e:
        session.rollback()
        print(f"   ❌ Failed to add columns: {e}")
        raise
    finally:
        session.close()

    # Get rotation start date as the effective_from date for all existing employment
    rotation_start = get_rotation_start_date()
    print(f"\n3️⃣ Using rotation start date as effective_from: {rotation_start}")

    # Backfill PersonHistory for current users
    session = SessionLocal()
    try:
        # Get users 1-10 (the positions in rotation)
        users = session.query(User).filter(User.id.between(1, 10)).all()
        print(f"\n4️⃣ Backfilling PersonHistory for {len(users)} users...")

        backfilled_count = 0
        for user in users:
            # Check if PersonHistory already exists for this user (any record, not just active)
            existing = session.query(PersonHistory).filter(PersonHistory.user_id == user.id).first()

            if existing:
                print(f"   ⚠️  User {user.name} (ID: {user.id}) already has person history, skipping")
                continue

            # Create PersonHistory entry
            # Initially: person_id == user_id (everyone is in their "original" position)
            person_history = PersonHistory(
                user_id=user.id,
                person_id=user.id,  # Currently person_id == user_id
                name=user.name,
                username=user.username,
                is_active=1,  # All current users are active
                effective_from=rotation_start,
                effective_to=None,  # NULL = currently employed
                created_at=datetime.utcnow(),
                created_by=None,  # System migration
            )

            session.add(person_history)
            backfilled_count += 1
            print(f"   ✅ Backfilled {user.name} (user_id={user.id}, person_id={user.id}) from {rotation_start}")

        session.commit()
        print(f"   📊 Backfilled {backfilled_count} PersonHistory records")
        print(f"   📊 Skipped {len(users) - backfilled_count} users (already had history)")

    except Exception as e:
        session.rollback()
        print(f"\n❌ Migration failed: {e}")
        raise
    finally:
        session.close()

    # Set User.person_id from active PersonHistory records
    print("\n5️⃣ Setting User.person_id from active PersonHistory records...")
    session = SessionLocal()
    try:
        # Find all active PersonHistory records (effective_to IS NULL)
        active_records = session.query(PersonHistory).filter(PersonHistory.effective_to.is_(None)).all()

        updated_count = 0
        for record in active_records:
            user = session.query(User).filter(User.id == record.user_id).first()
            if user:
                if user.person_id == record.person_id:
                    print(f"   ⚠️  User {user.name} (ID: {user.id}) already has person_id={record.person_id}, skipping")
                    continue
                old_value = user.person_id
                user.person_id = record.person_id
                updated_count += 1
                print(f"   ✅ User {user.name} (ID: {user.id}): person_id {old_value} → {record.person_id}")

        # Also clear person_id for inactive users who have no active PersonHistory
        all_users = session.query(User).all()
        for user in all_users:
            has_active_record = any(r.user_id == user.id for r in active_records)
            if not has_active_record and user.person_id is not None:
                print(f"   ✅ User {user.name} (ID: {user.id}): person_id {user.person_id} → NULL (no active record)")
                user.person_id = None
                updated_count += 1

        session.commit()
        print(f"   📊 Updated {updated_count} users")

    except Exception as e:
        session.rollback()
        print(f"\n❌ Step 5 failed: {e}")
        raise
    finally:
        session.close()

    print("\n✅ Person history migration completed successfully!")
    print("\n💡 Next steps:")
    print("   1. Test person history queries with app/core/schedule/person_history.py")
    print("   2. Update admin UI to manage person changes")
    print("   3. Update authorization to filter data by employment period")
    print("   4. Update year/month views to show correct person names per time period")


if __name__ == "__main__":
    migrate()
