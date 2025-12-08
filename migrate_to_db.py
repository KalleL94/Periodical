#!/usr/bin/env python3
"""
Migration script: Import persons from JSON to SQLite database.

Run this once to set up the database with existing person data.
Creates usernames based on names (lowercase, no spaces).

Usage:
    python migrate_to_db.py
"""

import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.database.database import create_tables, SessionLocal, User, UserRole
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


def create_username(name: str) -> str:
    """Create a username from a name."""
    # Lowercase, replace spaces with nothing
    username = name.lower().replace(" ", "").replace("å", "a").replace("ä", "a").replace("ö", "o")
    return username


def migrate():
    """Run the migration."""
    print("=" * 50)
    print("Migration: persons.json → SQLite database")
    print("=" * 50)
    
    # Create tables
    print("\n1. Creating database tables...")
    create_tables()
    print("   ✓ Tables created")
    
    # Load persons
    print("\n2. Loading persons.json...")
    try:
        persons = load_persons_json()
        print(f"   ✓ Found {len(persons)} persons")
    except FileNotFoundError as e:
        print(f"   ✗ Error: {e}")
        return False
    
    # Create database session
    db = SessionLocal()
    
    try:
        # Check if users already exist
        existing_count = db.query(User).count()
        if existing_count > 0:
            print(f"\n   ⚠ Database already has {existing_count} users.")
            response = input("   Delete existing users and re-import? (y/N): ")
            if response.lower() != 'y':
                print("   Migration cancelled.")
                return False
            db.query(User).delete()
            db.commit()
            print("   ✓ Existing users deleted")
        
        # Import persons
        print("\n3. Importing users...")
        default_password = "London1"  # ÄNDRA DETTA I PRODUKTION
        
        created_users = []
        for person in persons:
            username = create_username(person["name"])
            
            # First person (id=1) becomes admin
            role = UserRole.ADMIN if person["id"] == 6 else UserRole.USER
            
            user = User(
                id=person["id"],
                username=username,
                password_hash=get_password_hash(default_password),
                name=person["name"],
                role=role,
                wage=person["wage"],
                vacation=person.get("vacation", {}),
            )
            db.add(user)
            created_users.append((username, person["name"], role.value))
        
        db.commit()
        print(f"   ✓ Created {len(created_users)} users")
        
        # Print summary
        print("\n" + "=" * 50)
        print("MIGRATION COMPLETE")
        print("=" * 50)
        print("\nCreated users:")
        print("-" * 50)
        print(f"{'Username':<15} {'Name':<20} {'Role':<10}")
        print("-" * 50)
        for username, name, role in created_users:
            print(f"{username:<15} {name:<20} {role:<10}")
        
        print("\n" + "=" * 50)
        print(f"DEFAULT PASSWORD FOR ALL USERS: {default_password}")
        print("⚠  CHANGE PASSWORDS AFTER FIRST LOGIN!")
        print("=" * 50)
        
        return True
        
    except Exception as e:
        print(f"\n   ✗ Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)