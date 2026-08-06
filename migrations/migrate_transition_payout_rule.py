#!/usr/bin/env python3
"""
Migration script adding the vacation payout rule to employment transitions.

The payout at the end of a consultant engagement can follow either statutory
rule, and which one applies depends on the consultant employer's agreement:

- "sammalone" (Semesterlagen 16 a): 4.6% of the monthly salary per unused day
  plus the supplement, plus 0.5% of that day's earning year's variable pay.
- "procent" (Semesterlagen 16 b): 12% of the earning year's pay, spread over
  the days that year earned. Required when pay is variable to a substantial
  degree.

Existing transitions keep the same-pay rule, which is what they were computed
under before this column existed.

Usage:
    python migrations/migrate_transition_payout_rule.py
"""

from sqlalchemy import create_engine, inspect, text

DATABASE_URL = "sqlite:///./app/database/schedule.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

COLUMN = "vacation_payout_rule"
DDL = "VARCHAR(20) NOT NULL DEFAULT 'sammalone'"


def migrate():
    """Run the migration."""
    print("Starting transition payout rule migration...")

    inspector = inspect(engine)
    if "employment_transitions" not in inspector.get_table_names():
        print("   Table employment_transitions does not exist, nothing to do")
        return

    columns = {c["name"] for c in inspector.get_columns("employment_transitions")}
    if COLUMN in columns:
        print(f"   Column {COLUMN} already exists, skipping")
        return

    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE employment_transitions ADD COLUMN {COLUMN} {DDL}"))
        rows = conn.execute(text("SELECT COUNT(*) FROM employment_transitions")).scalar()

    print(f"   Added column {COLUMN}")
    print(f"Migration complete: {rows} existing transitions kept on 'sammalone'")


if __name__ == "__main__":
    migrate()
