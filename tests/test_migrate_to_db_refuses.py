"""The seeder must never touch a database file that already exists."""

import sys
from pathlib import Path

from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).parent.parent))

from migrations import migrate_to_db  # noqa: E402


def test_existing_database_is_refused_and_left_intact(tmp_path, monkeypatch, capsys):
    db_file = tmp_path / "schedule.db"
    db_file.write_bytes(b"precious")
    monkeypatch.setattr(migrate_to_db, "engine", create_engine(f"sqlite:///{db_file}"))

    assert migrate_to_db.migrate() is False

    assert db_file.read_bytes() == b"precious"
    assert "already exists" in capsys.readouterr().out
