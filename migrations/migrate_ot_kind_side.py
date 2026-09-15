#!/usr/bin/env python3
"""Backfill overtime_shifts.kind/side from is_extension, then drop is_extension.

migrate_schema.py adds the two columns from the models, but it never rewrites
existing rows and never drops anything, so this is the data half and lives in its
own file the way that script's docstring prescribes.

Three steps, each skipped when it has already happened, so the script is safe to
run twice and safe to run on a database that has only ever known the new shape:

1. Rows still carrying the column default get kind/side from is_extension.
2. Two unique indexes, one per owner column, enforce one row per
   (owner, date, kind, side). Each is a no-op for rows where its owner column is
   NULL, which is what we want since exactly one of the two is ever set.
3. is_extension goes. SQLite 3.35+ supports ALTER TABLE DROP COLUMN.

Usage:
    python migrations/migrate_ot_kind_side.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402

INDEXES = {
    "ix_ot_user_day_kind_side": "CREATE UNIQUE INDEX ix_ot_user_day_kind_side"
    " ON overtime_shifts (user_id, date, kind, side)",
    "ix_ot_sub_day_kind_side": "CREATE UNIQUE INDEX ix_ot_sub_day_kind_side"
    " ON overtime_shifts (substitute_id, date, kind, side)",
}


def migrate(engine) -> dict[str, int]:
    """Apply the three steps. Returns what each one actually did."""
    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("overtime_shifts")}
    existing_indexes = {i["name"] for i in inspector.get_indexes("overtime_shifts")}

    result = {"backfilled": 0, "indexes_created": 0, "column_dropped": 0}

    with engine.begin() as connection:
        if "is_extension" in columns:
            backfill = connection.execute(
                text(
                    "UPDATE overtime_shifts SET kind = 'ot', side = CASE WHEN is_extension THEN 'after' ELSE 'full' END"
                )
            )
            result["backfilled"] = backfill.rowcount

        for name, ddl in INDEXES.items():
            if name not in existing_indexes:
                connection.exec_driver_sql(ddl)
                result["indexes_created"] += 1

        if "is_extension" in columns:
            connection.exec_driver_sql('ALTER TABLE overtime_shifts DROP COLUMN "is_extension"')
            result["column_dropped"] = 1

    return result


def main() -> int:
    from app.database.database import DATABASE_URL, engine

    print(f"Migrating {DATABASE_URL}")
    result = migrate(engine)
    print(
        f"  rows backfilled:  {result['backfilled']}\n"
        f"  indexes created:  {result['indexes_created']}\n"
        f"  is_extension dropped: {bool(result['column_dropped'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
