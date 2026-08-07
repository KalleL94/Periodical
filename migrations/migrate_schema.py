#!/usr/bin/env python3
"""Bring a database's schema up to the current models.

Creates tables the models declare but the database lacks, then adds columns the
models declare but an existing table lacks. Idempotent: a database already at
the current schema comes out unchanged, so it is safe to run before every
deploy and safe to run twice.

This replaces the per-change migration scripts that used to live here, one file
per column with the same sqlite3 connect / PRAGMA table_info / ALTER TABLE
boilerplate in each. Adding a column to a model needs no file at all now: the
next run of this script picks it up. Data migrations, the ones that rewrite
existing rows rather than change the shape of a table, are still one-off
scripts and still belong in their own file.

Targets whatever `DATABASE_URL` points at, exactly like the app, so running it
from the deployment directory migrates the database the app will open.

Usage:
    python migrations/migrate_schema.py [--dry-run]
"""

import argparse
import enum
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect  # noqa: E402

from app.database.database import DATABASE_URL, Base, engine  # noqa: E402


class UnmigratableColumn(Exception):
    """A missing column that ALTER TABLE cannot add without inventing data."""


def _sql_literal(value: object) -> str:
    """Render a Python default as a SQL literal for a DEFAULT clause."""
    if isinstance(value, enum.Enum):
        value = value.value
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def add_column_sql(table_name: str, column) -> str:
    """Return the ALTER TABLE statement that adds `column` to `table_name`.

    A NOT NULL column needs a DEFAULT, because the rows already in the table
    have to be given a value. The models carry that value as a Python-side
    default, so it is copied into the DDL. A NOT NULL column with no default,
    or one whose default is computed per row, has no right answer here and
    raises instead of guessing one.

    A UNIQUE column is added without its constraint: SQLite rejects ADD COLUMN
    with UNIQUE. The hand-written migrations this replaces did the same. Add the
    index by hand if a new unique column ever needs to be enforced at the
    database rather than in the application.
    """
    ddl = f'"{column.name}" {column.type.compile(engine.dialect)}'

    if column.nullable:
        return f'ALTER TABLE "{table_name}" ADD COLUMN {ddl}'

    default = column.default
    if default is None or default.is_callable or not default.is_scalar:
        raise UnmigratableColumn(
            f"{table_name}.{column.name} is NOT NULL with no scalar default. "
            f"Give the model a default, or add the column in its own migration."
        )
    return f'ALTER TABLE "{table_name}" ADD COLUMN {ddl} NOT NULL DEFAULT {_sql_literal(default.arg)}'


def pending_changes() -> tuple[list[str], list[str]]:
    """Return (missing table names, ALTER TABLE statements) for the current database."""
    inspector = inspect(engine)
    present = set(inspector.get_table_names())

    missing_tables = []
    statements = []
    for table in Base.metadata.sorted_tables:
        if table.name not in present:
            missing_tables.append(table.name)
            continue
        existing = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in existing:
                statements.append(add_column_sql(table.name, column))

    return missing_tables, statements


def migrate(dry_run: bool = False) -> int:
    """Apply the pending schema changes. Returns the number of changes made."""
    missing_tables, statements = pending_changes()

    if not missing_tables and not statements:
        print(f"Schema is up to date: {DATABASE_URL}")
        return 0

    for name in missing_tables:
        print(f"{'Would create' if dry_run else 'Creating'} table {name}")
    for statement in statements:
        print(f"{'Would run' if dry_run else 'Running'}: {statement}")

    if dry_run:
        return len(missing_tables) + len(statements)

    if missing_tables:
        Base.metadata.create_all(bind=engine)
    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.exec_driver_sql(statement)

    print(f"Applied {len(missing_tables) + len(statements)} change(s) to {DATABASE_URL}")
    return len(missing_tables) + len(statements)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Print the changes without applying them")
    args = parser.parse_args()

    try:
        migrate(dry_run=args.dry_run)
    except UnmigratableColumn as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
