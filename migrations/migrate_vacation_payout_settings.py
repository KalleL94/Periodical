#!/usr/bin/env python3
"""
Migration script to move the vacation payout settings onto users.

Adds vacation_fixed_per_day, vacation_variable_payout and
vacation_variable_payout_month. They briefly lived in custom_rates["vacation"],
which is the wrong home: custom_rates is versioned through RateHistory, so a
wage revision would open a new rate period and drag a payout routine along with
it. Any values already stored there are carried over and then removed.

The defaults reproduce today's behaviour exactly: no flat amount per day (the
fixed percentage still applies) and the variable part paid per vacation day.

Usage:
    python migrations/migrate_vacation_payout_settings.py
"""

import json

from sqlalchemy import create_engine, inspect, text

DATABASE_URL = "sqlite:///./app/database/schedule.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

NEW_COLUMNS = (
    ("vacation_fixed_per_day", "FLOAT"),
    ("vacation_variable_payout", "VARCHAR(10) NOT NULL DEFAULT 'per_day'"),
    ("vacation_variable_payout_month", "INTEGER"),
)

# custom_rates["vacation"] key -> users column
CARRY_OVER = {
    "fixed_per_day": "vacation_fixed_per_day",
    "variable_payout": "vacation_variable_payout",
    "variable_payout_month": "vacation_variable_payout_month",
}


def migrate():
    """Run the migration."""
    print("Starting vacation payout settings migration...")

    columns = {c["name"] for c in inspect(engine).get_columns("users")}
    added = 0
    with engine.begin() as conn:
        for name, ddl in NEW_COLUMNS:
            if name in columns:
                print(f"   Column {name} already exists, skipping")
                continue
            conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {ddl}"))
            print(f"   Added column {name}")
            added += 1

    moved = 0
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id, custom_rates FROM users WHERE custom_rates IS NOT NULL")).fetchall()
        for user_id, raw in rows:
            try:
                rates = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                continue
            vacation = (rates or {}).get("vacation") or {}
            present = {key: vacation[key] for key in CARRY_OVER if vacation.get(key) is not None}
            if not present:
                continue

            assignments = ", ".join(f"{CARRY_OVER[key]} = :{key}" for key in present)
            conn.execute(text(f"UPDATE users SET {assignments} WHERE id = :uid"), {**present, "uid": user_id})

            for key in present:
                vacation.pop(key, None)
            rates["vacation"] = vacation
            conn.execute(
                text("UPDATE users SET custom_rates = :rates WHERE id = :uid"),
                {"rates": json.dumps(rates), "uid": user_id},
            )
            print(f"   Moved {sorted(present)} off custom_rates for user {user_id}")
            moved += 1

    print(f"Migration complete: {added} columns added, {moved} users carried over")


if __name__ == "__main__":
    migrate()
