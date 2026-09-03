#!/usr/bin/env python3
"""Add the optional start_time/end_time window to oncall_overrides.

On-call used to be a whole day: the pay calculation always ran 00:00 to 24:00.
Two people sharing one day (one takes the morning, the other the evening and
night) had no way to say so, so the day was paid twice over.

The two new columns are nullable and both NULL keeps the old behaviour, which
is what every existing row means. Nothing needs backfilling.

Idempotent: a database that already has the columns comes out unchanged.

Usage:
    python migrations/migrate_oncall_shift_times.py [--dry-run]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402

from app.database.database import DATABASE_URL, engine  # noqa: E402

TABLE = "oncall_overrides"
COLUMNS = ("start_time", "end_time")


def migrate(dry_run: bool = False) -> list[str]:
    """Add any missing window columns. Returns the columns added."""
    existing = {col["name"] for col in inspect(engine).get_columns(TABLE)}
    missing = [name for name in COLUMNS if name not in existing]

    for name in missing:
        print(f"  adding {TABLE}.{name}")
        if dry_run:
            continue
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {name} VARCHAR(5)"))

    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args()

    print(f"Database: {DATABASE_URL}")
    added = migrate(dry_run=args.dry_run)

    if not added:
        print("Nothing to do: the window columns already exist.")
    elif args.dry_run:
        print(f"Dry run: {len(added)} column(s) would be added.")
    else:
        print(f"Done: added {', '.join(added)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
