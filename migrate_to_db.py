#!/usr/bin/env python3
"""
Migration script: Import persons from JSON to SQLite database.

Run this once to set up the database with existing person data.
Creates usernames based on names (lowercase, no spaces).

Usage:
    python migrate_to_db.py

This will:
1. Delete existing database (if any)
2. Create fresh tables
3. Import all persons as regular users
4. Create a separate admin account
"""

import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.database.database import engine, Base, SessionLocal, User, UserRole
from app.auth.auth import get_password_hash


def load_persons_json():
    """Load persons from JSON file."""
    persons_path = Path("data/persons.json")
    if not persons_path.exists():
        # Try alternative location
        persons_path = Path("persons.json")
    
    if not persons_path.exists():
        raise FileNotFoundError("Could not find persons.json")
    
    with open(persons_path, "r", encoding="utf-8") as f:
        return json.load(f)

try:
    persons = load_persons_json()
    print(f"   [OK] Found {len(persons)} persons")
except FileNotFoundError as e:
    print(f"   [ERROR] {e}")

ADMIN_ACCOUNT = {
    "username": "admin",
    "name": "Administrator",
    "wage": 0,
}

DEFAULT_PASSWORD = "London1"  # ÄNDRA DETTA I PRODUKTION

def delete_existing_db():
    """Delete existing database"""
    db_path = Path("app/database/schedule.db")
    if db_path.exists():
        db_path.unlink()
        print(f" Deleted existing database: {db_path}")
    else:
        print(f" No existing database to delete")

def create_tables():
    """Create tables"""
    Base.metadata.create_all(bind=engine)
    print("Created tables")

def load_vacation_from_json() -> dict:
    """Läs semester från persons.json om den finns."""
    persons_path = Path("data/persons.json")
    if not persons_path.exists():
        return {}
    
    try:
        data = json.loads(persons_path.read_text(encoding="utf-8"))
        return {p["id"]: p.get("vacation", {}) for p in data}
    except Exception as e:
        print(f"  Varning: Kunde inte läsa semester från persons.json: {e}")
        return {}

def create_username(name: str) -> str:
    """Create a username from a name."""
    # Lowercase, replace spaces with nothing
    username = name.lower().replace(" ", "").replace("å", "a").replace("ä", "a").replace("ö", "o")
    return username


def migrate():
    """Run the migration."""
    print("\n" + "=" * 50)
    print("MIGRATION: persons.json -> SQLite")
    print("=" * 50 + "\n")

    delete_existing_db()
    
    # Create tables
    print("\n1. Creating database tables...")
    create_tables()
    print("   [OK] Tables created")
    
    vacation_data = load_vacation_from_json()
    # Create database session
    db = SessionLocal()
    
    try:
        # Check if users already exist
        existing_count = db.query(User).count()
        if existing_count > 0:
            print(f"\n   [WARNING] Database already has {existing_count} users.")
            response = input("   Delete existing users and re-import? (y/N): ")
            if response.lower() != 'y':
                print("   Migration cancelled.")
                return False
            db.query(User).delete()
            db.commit()
            print("   [OK] Existing users deleted")
        
        # Import persons
        print("\n3. Importing users...")
        
        created_users = []
        for person in persons:
            vacation = vacation_data.get(person["id"])
            
            
            user = User(
                id=person["id"],
                username=person["username"],
                password_hash=get_password_hash(DEFAULT_PASSWORD),
                name=person["name"],
                role=UserRole.USER,
                wage=person["wage"],
                vacation=vacation,
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