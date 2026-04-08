"""
Migration: Lägg till left_at-kolumn på absences-tabellen.

left_at = NULL    => heldag frånvaro (befintligt beteende)
left_at = "HH:MM" => partiell frånvaro, klockslag när personen slutade jobba

Kör: python migrate_absence_add_hours.py
"""

import os
import sqlite3

DB_PATH = os.environ.get("DATABASE_PATH", "app/database/schedule.db")


def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(absences)")
    columns = [row[1] for row in cursor.fetchall()]

    if "left_at" not in columns:
        cursor.execute("ALTER TABLE absences ADD COLUMN left_at TEXT")
        print("Kolumnen 'left_at' har lagts till i absences-tabellen.")
    else:
        print("Kolumnen 'left_at' finns redan, hoppar över.")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
