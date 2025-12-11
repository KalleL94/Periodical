# migrate_overtime.py
"""
Migration script: Add overtime_shifts table to existing database.

Usage:
    python migrate_overtime.py
"""

import sqlite3
import sys
from pathlib import Path

def migrate_overtime_table():
    """Add overtime_shifts table and index to the database."""
    
    # Define path relative to project root
    db_path = Path("app/database/schedule.db")
    
    if not db_path.exists():
        print(f"[ERROR] Database not found at {db_path}")
        print("Please ensure you are running this from the project root and the database exists.")
        sys.exit(1)
        
    print(f"Connecting to database: {db_path}")
    
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Enable foreign key constraints
        cursor.execute("PRAGMA foreign_keys = ON;")
        
        print("1. Creating table 'overtime_shifts'...")
        
        # Create table if it doesn't exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS overtime_shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                date DATE NOT NULL,
                start_time TIME NOT NULL,
                end_time TIME NOT NULL,
                hours FLOAT NOT NULL,
                ot_pay FLOAT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                created_by INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (created_by) REFERENCES users(id)
            );
        """)
        
        print("2. Creating index 'idx_overtime_user_date'...")
        
        # Create index if it doesn't exist
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_overtime_user_date 
            ON overtime_shifts(user_id, date);
        """)
        
        conn.commit()
        print("\n[OK] Migration successful: 'overtime_shifts' table ready.")
        
    except sqlite3.Error as e:
        print(f"\n[ERROR] SQLite error: {e}")
        if conn:
            conn.rollback()
        sys.exit(1)
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    migrate_overtime_table()
