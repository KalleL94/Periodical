# migrate_add_password_change.py
"""
Migration: Lägg till must_change_password kolumn i users tabellen.

Kör detta script för att uppdatera befintlig databas med det nya fältet.
Alla befintliga användare kommer få must_change_password=1 (måste byta lösenord).

Usage:
    python migrate_add_password_change.py
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("app/database/schedule.db")

def migrate():
    """Lägg till must_change_password kolumn."""
    if not DB_PATH.exists():
        print(f"[ERROR] Databas hittades inte: {DB_PATH}")
        print("  Kör 'python migrate_to_db.py' först för att skapa databasen.")
        return False

    print("\n" + "=" * 60)
    print("MIGRATION: Lägg till must_change_password kolumn")
    print("=" * 60)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # Kontrollera om kolumnen redan finns
        cursor.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cursor.fetchall()]

        if "must_change_password" in columns:
            print("\n[OK] Kolumnen 'must_change_password' finns redan!")
            return True

        # Lägg till kolumn
        print("\nLägger till kolumn 'must_change_password'...")
        cursor.execute("""
            ALTER TABLE users
            ADD COLUMN must_change_password INTEGER DEFAULT 1 NOT NULL
        """)

        # Sätt alla befintliga användare till must_change_password=1
        cursor.execute("UPDATE users SET must_change_password = 1")
        affected_rows = cursor.rowcount

        conn.commit()

        print(f"[OK] Kolumn tillagd!")
        print(f"[OK] {affected_rows} användare uppdaterade (must_change_password=1)")

        # Visa status
        cursor.execute("SELECT id, username, name, role, must_change_password FROM users ORDER BY id")
        users = cursor.fetchall()

        print("\nAnvändare i databasen:")
        print(f"{'ID':<4} {'Username':<12} {'Name':<20} {'Role':<8} {'Must Change'}")
        print("-" * 60)
        for user in users:
            user_id, username, name, role, must_change = user
            must_change_str = "Ja" if must_change == 1 else "Nej"
            print(f"{user_id:<4} {username:<12} {name:<20} {role:<8} {must_change_str}")

        print("\n" + "=" * 60)
        print("MIGRATION KLAR")
        print("=" * 60)
        print("Alla användare måste nu byta lösenord vid nästa inloggning.")
        print("=" * 60)

        return True

    except sqlite3.Error as e:
        print(f"\n[ERROR] Databasfel: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
