#!/usr/bin/env python3
"""Drop the derived `variable_lump_pct` values from users.vacation_settings.

`variable_lump_pct` used to be computed and never read: the payout code always
derived it as 0.5% per paid day, and the per-year settings migration copied the
value that happened to be on the user row into every entry it created. Those
entries now hold a frozen number nobody chose, most of them the column default of
0.12 and the rest whatever one year's entitlement worked out to.

The field is read now, and it means "the agreement states this share of the
earning year's variable pay". An absent value derives the statutory figure, which
is what every one of these users has actually been getting. Leaving the copies in
place would turn a stale artefact into a stated intent and change the payout for
anyone who has one, so they go.

The `users.vacation_variable_lump_pct` column is left alone. It is NOT NULL with a
0.12 default and so cannot express "not configured"; nothing reads it any more,
and dropping a column in SQLite is a table rebuild for no gain. Configure the
share per vacation year or per employer in `vacation_settings` instead.

Idempotent: a database with no derived values left comes out unchanged.

Usage:
    python migrations/migrate_clear_derived_lump_pct.py [--dry-run]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm.attributes import flag_modified  # noqa: E402

from app.database.database import DATABASE_URL, SessionLocal, User  # noqa: E402

FIELD = "variable_lump_pct"


def clear(session, dry_run: bool = False) -> int:
    """Strip FIELD from every vacation_settings entry. Returns the users touched."""
    touched = 0
    for user in session.query(User).all():
        settings = user.vacation_settings or {}
        hits = {key: entry[FIELD] for key, entry in settings.items() if isinstance(entry, dict) and FIELD in entry}
        if not hits:
            continue

        touched += 1
        pretty = ", ".join(f"{key}={value}" for key, value in sorted(hits.items()))
        print(f"  user {user.id} ({user.username}): removing {pretty}")
        if dry_run:
            continue

        user.vacation_settings = {
            key: ({k: v for k, v in entry.items() if k != FIELD} if isinstance(entry, dict) else entry)
            for key, entry in settings.items()
        }
        flag_modified(user, "vacation_settings")

    if not dry_run:
        session.commit()
    return touched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args()

    print(f"Database: {DATABASE_URL}")
    session = SessionLocal()
    try:
        touched = clear(session, dry_run=args.dry_run)
    finally:
        session.close()

    if not touched:
        print("Nothing to do: no derived values stored.")
    elif args.dry_run:
        print(f"Dry run: {touched} user(s) would change.")
    else:
        print(f"Done: {touched} user(s) updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
