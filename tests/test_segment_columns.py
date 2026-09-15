"""The new columns exist with the shape migrate_schema.py can add unaided."""

import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database.database import Base  # noqa: E402


def _columns(table):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return {c["name"]: c for c in inspect(engine).get_columns(table)}


def test_overtime_kind_and_side_are_not_null_with_defaults():
    cols = _columns("overtime_shifts")
    assert cols["kind"]["nullable"] is False
    assert cols["side"]["nullable"] is False


def test_shift_override_gains_nullable_times_and_label():
    cols = _columns("shift_overrides")
    for name in ("start_time", "end_time", "label"):
        assert cols[name]["nullable"] is True


def test_migrate_schema_can_add_the_new_columns_unaided():
    """A NOT NULL column needs a Python-side default or migrate_schema raises."""
    from migrations.migrate_schema import add_column_sql

    table = Base.metadata.tables["overtime_shifts"]
    for name in ("kind", "side"):
        sql = add_column_sql("overtime_shifts", table.columns[name])
        assert "NOT NULL" in sql
        assert "DEFAULT" in sql
