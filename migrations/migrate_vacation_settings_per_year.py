#!/usr/bin/env python3
"""
Migration script versioning the vacation payout settings per vacation year.

The five payout settings (flat amount per day, whether the variable part is paid
per day or as a lump, which month the lump lands in, the lump percentage and the
payroll lag) lived as single columns on users, so changing them for one year
silently rewrote every other year too. They now live in users.vacation_settings,
a JSON object keyed by the vacation year the setting starts applying in, together
with the statutory payout rule (16 a or 16 b) that used to sit on the transition.

Nothing is copied into the new column: a year with no entry inherits the closest
earlier year that has one, and with none at all falls back to the existing
columns. Every user therefore keeps exactly today's behaviour until they
configure a specific year.

Usage:
    python migrations/migrate_vacation_settings_per_year.py
"""

import json

from sqlalchemy import create_engine, inspect, text

DATABASE_URL = "sqlite:///./app/database/schedule.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

COLUMN = "vacation_settings"
DDL = "JSON DEFAULT '{}'"


def migrate():
    """Run the migration."""
    print("Starting per-year vacation settings migration...")

    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("users")}

    if COLUMN in columns:
        print(f"   Column {COLUMN} already exists, skipping")
    else:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE users ADD COLUMN {COLUMN} {DDL}"))
            conn.execute(text(f"UPDATE users SET {COLUMN} = '{{}}' WHERE {COLUMN} IS NULL"))
        print(f"   Added column {COLUMN}")

    # The transition's own rule column is superseded: the payout now inherits the
    # rule from the vacation year, so two places could no longer disagree.
    transition_columns = {c["name"] for c in inspector.get_columns("employment_transitions")}
    if "vacation_payout_rule" in transition_columns:
        with engine.begin() as conn:
            rows = conn.execute(
                text("SELECT user_id, transition_date, vacation_payout_rule FROM employment_transitions")
            ).fetchall()
            for user_id, transition_date, rule in rows:
                if not rule or rule == "sammalone":
                    continue
                # Carry a non-default choice onto the vacation year the transition
                # falls in, so an already-configured transition keeps its rule.
                start_month, raw = conn.execute(
                    text("SELECT vacation_year_start_month, vacation_settings FROM users WHERE id = :uid"),
                    {"uid": user_id},
                ).one()
                try:
                    settings = json.loads(raw) if isinstance(raw, str) and raw.strip() else (raw or {})
                except ValueError:
                    settings = {}

                year, month = int(str(transition_date)[:4]), int(str(transition_date)[5:7])
                vacation_year = str(year if month >= (start_month or 4) else year - 1)
                settings[vacation_year] = {**settings.get(vacation_year, {}), "payout_rule": rule}

                conn.execute(
                    text("UPDATE users SET vacation_settings = :settings WHERE id = :uid"),
                    {"settings": json.dumps(settings), "uid": user_id},
                )
                print(f"   Carried rule '{rule}' to vacation year {vacation_year} for user {user_id}")

            conn.execute(text("ALTER TABLE employment_transitions DROP COLUMN vacation_payout_rule"))
        print("   Dropped employment_transitions.vacation_payout_rule")

    print("Migration complete")


if __name__ == "__main__":
    migrate()
