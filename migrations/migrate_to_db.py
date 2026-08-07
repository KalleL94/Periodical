# migrate_to_db.py
"""
Seed a fresh database with one account per rotation position, plus an admin.

For a new installation only. It DELETES the configured database first, so it is
not a migration in the sense migrate_schema.py is: run that one against a
database with data in it.

Every account is created with must_change_password=1, so the shared initial
password below is usable exactly once each.

Usage:
    python migrations/migrate_to_db.py
"""

import sys
from pathlib import Path

# Add project root to path. This file used to live at the repository root, where
# its own directory was the project root; from migrations/ it needs one level up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth.auth import get_password_hash
from app.core.constants import MAX_PERSONS
from app.database.database import DATABASE_URL, Base, SessionLocal, User, UserRole, engine

#: Starting wage for a seeded account, in SEK per month. Every account gets the
#: same one; real wages are set per user afterwards, and the wage history takes
#: over from the first revision.
DEFAULT_WAGE = 30000

#: One account per rotation position. This used to be read from
#: data/persons.json, which after anonymisation held exactly these rows: userNN,
#: "Person N", the same wage and no vacation. The file was a fixture pretending
#: to be configuration, so the fixture lives here now, at its only reader.
PERSONS = [
    {"id": pid, "username": f"user{pid:02d}", "name": f"Person {pid}", "wage": DEFAULT_WAGE}
    for pid in range(1, MAX_PERSONS + 1)
]

ADMIN_ACCOUNT = {
    "username": "admin",
    "name": "Administrator",
    "wage": 0,
}

DEFAULT_PASSWORD = "London1"  # ÄNDRA DETTA I PRODUKTION


def delete_existing_db():
    """Delete the configured SQLite database file, if there is one.

    Reads the path off the engine rather than hardcoding
    app/database/schedule.db: the app honours DATABASE_URL, and a seeder that
    deleted a different file than the one it then writes to would wipe the
    wrong database.
    """
    if engine.url.get_backend_name() != "sqlite" or not engine.url.database:
        print(f" Not a SQLite file database, nothing to delete: {DATABASE_URL}")
        return

    db_path = Path(engine.url.database)
    if db_path.exists():
        db_path.unlink()
        print(f" Deleted existing database: {db_path}")
    else:
        print(" No existing database to delete")


def create_tables():
    """Create tables"""
    Base.metadata.create_all(bind=engine)
    print("Created tables")


def migrate():
    """Run the migration."""
    print("\n" + "=" * 50)
    print("MIGRATION: seed accounts -> SQLite")
    print("=" * 50 + "\n")

    delete_existing_db()

    # Create tables
    print("\n1. Creating database tables...")
    create_tables()
    print("   [OK] Tables created")

    # Create database session
    db = SessionLocal()

    try:
        # Check if users already exist
        existing_count = db.query(User).count()
        if existing_count > 0:
            print(f"\n   [WARNING] Database already has {existing_count} users.")
            response = input("   Delete existing users and re-import? (y/N): ")
            if response.lower() != "y":
                print("   Migration cancelled.")
                return False
            db.query(User).delete()
            db.commit()
            print("   [OK] Existing users deleted")

        # Import persons
        print("\n3. Importing users...")

        created_users = []
        for person in PERSONS:
            user = User(
                id=person["id"],
                username=person["username"],
                password_hash=get_password_hash(DEFAULT_PASSWORD),
                name=person["name"],
                role=UserRole.USER,
                wage=person["wage"],
                vacation={},
                must_change_password=1,  # Force password change on first login
            )
            created_users.append(user)
            db.add(user)
            print(f"  + User {person['id']:2d}: {person['username']:10s} ({person['name']})")

        admin = User(
            id=0,
            username=ADMIN_ACCOUNT["username"],
            password_hash=get_password_hash("Banan1"),
            name=ADMIN_ACCOUNT["name"],
            role=UserRole.ADMIN,
            wage=ADMIN_ACCOUNT["wage"],
            vacation={},
            must_change_password=1,  # Force password change on first login
        )
        db.add(admin)
        print(f"  + Admin  : {ADMIN_ACCOUNT['username']:10s} ({ADMIN_ACCOUNT['name']}) [ADMIN]")

        db.commit()
        print(f"   [OK] Created {len(created_users)} users")

        # Print summary
        print("\n" + "=" * 50)
        print("MIGRATION COMPLETE")
        print("=" * 50)
        print(f"DEFAULT PASSWORD FOR ALL USERS: {DEFAULT_PASSWORD}")
        print("[WARNING] CHANGE PASSWORDS AFTER FIRST LOGIN!")
        print("=" * 50)

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
