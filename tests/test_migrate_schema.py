"""Checks for migrations/migrate_schema.py, which replaces the per-column migrations.

The script decides what a database is missing and writes the DDL to add it, so the
thing worth testing is that a column removed from a database comes back with the
right shape: a NOT NULL column carrying the model's default down onto rows that
already exist, and a nullable one added plainly.
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, String, create_engine, inspect, text
from sqlalchemy.orm import declarative_base

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database.database import Base  # noqa: E402
from migrations import migrate_schema  # noqa: E402


@pytest.fixture
def db_engine(tmp_path, monkeypatch):
    """A file-backed SQLite database at the current schema, wired into the script."""
    engine = create_engine(f"sqlite:///{tmp_path / 'schema.db'}")
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(migrate_schema, "engine", engine)
    return engine


def _drop_column(engine, table, column):
    with engine.begin() as connection:
        connection.exec_driver_sql(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')


def _columns(engine, table):
    return {column["name"]: column for column in inspect(engine).get_columns(table)}


def test_missing_not_null_column_is_restored_with_its_default(db_engine):
    with db_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users (username, password_hash, name, role, wage, wage_type, is_active, "
            "must_change_password, vacation_year_start_month, vacation_days_per_year, "
            "vacation_variable_payout, vacation_variable_lump_pct, vacation_variable_lump_lag_months, "
            "language) VALUES ('u', 'h', 'N', 'user', 30000, 'monthly', 1, 0, 4, 25, 'per_day', 0.12, 1, 'sv')"
        )
    _drop_column(db_engine, "users", "language")
    assert "language" not in _columns(db_engine, "users")

    assert migrate_schema.migrate() == 1

    restored = _columns(db_engine, "users")["language"]
    assert restored["nullable"] is False
    with db_engine.connect() as connection:
        # The pre-existing row has to come out of the migration with a value, or the
        # NOT NULL column could not have been added at all.
        assert connection.execute(text("SELECT language FROM users")).scalar() == "sv"


def test_missing_nullable_column_is_restored(db_engine):
    _drop_column(db_engine, "users", "parental_leave")

    assert migrate_schema.migrate() == 1

    assert _columns(db_engine, "users")["parental_leave"]["nullable"] is True


def test_missing_table_is_created(db_engine):
    with db_engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE login_attempts")

    assert migrate_schema.migrate() == 1

    assert "login_attempts" in inspect(db_engine).get_table_names()


def test_up_to_date_database_is_left_alone(db_engine):
    assert migrate_schema.migrate() == 0


def test_dry_run_changes_nothing(db_engine):
    _drop_column(db_engine, "users", "parental_leave")

    assert migrate_schema.migrate(dry_run=True) == 1

    assert "parental_leave" not in _columns(db_engine, "users")


def test_not_null_column_without_a_default_is_refused(db_engine):
    """Rather than invent a value for rows that already exist, the script stops."""
    base = declarative_base()

    class _Widget(base):
        __tablename__ = "widgets"
        id = Column(Integer, primary_key=True)
        label = Column(String(20), nullable=False)

    with pytest.raises(migrate_schema.UnmigratableColumn):
        migrate_schema.add_column_sql("widgets", _Widget.__table__.c.label)
