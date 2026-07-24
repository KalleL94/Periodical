#!/usr/bin/env python3
"""
Migration script to add the payslip_overrides table.

Creates the table only. Nothing to backfill: without an override row the month
is priced exactly as before, so this migration is a no-op for existing data.

Usage:
    python migrations/migrate_payslip_overrides.py
"""

from sqlalchemy import create_engine, inspect

from app.database.database import Base, PayslipOverride

DATABASE_URL = "sqlite:///./app/database/schedule.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def migrate():
    """Run the migration."""
    print("Starting payslip_overrides migration...")

    if inspect(engine).has_table(PayslipOverride.__tablename__):
        print(f"   Table {PayslipOverride.__tablename__} already exists, nothing to do")
        return

    Base.metadata.create_all(bind=engine, tables=[PayslipOverride.__table__])
    print(f"   Created table {PayslipOverride.__tablename__}")
    print("Migration complete")


if __name__ == "__main__":
    migrate()
