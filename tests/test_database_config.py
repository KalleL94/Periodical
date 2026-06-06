import os
import subprocess
import sys

import app.database.database as db_module


def test_database_url_defaults_to_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert db_module.get_database_url() == db_module.DEFAULT_DATABASE_URL


def test_database_url_uses_environment_override(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@example.test/periodical")

    assert db_module.get_database_url() == "postgresql://user:pass@example.test/periodical"


def test_sqlite_engine_uses_thread_connect_arg():
    assert db_module.get_engine_connect_args("sqlite:///./app/database/schedule.db") == {"check_same_thread": False}
    assert db_module.get_engine_connect_args("sqlite:///:memory:") == {"check_same_thread": False}


def test_non_sqlite_engine_has_no_sqlite_connect_args():
    assert db_module.get_engine_connect_args("postgresql://user:pass@example.test/periodical") == {}


def test_module_import_uses_database_url_environment_override(tmp_path):
    db_path = tmp_path / "custom.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"

    result = subprocess.run(
        [sys.executable, "-c", "import app.database.database as db; print(db.DATABASE_URL)"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == f"sqlite:///{db_path}"
