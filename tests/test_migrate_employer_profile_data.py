"""Checks for the one-off employer-profile data script."""

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).parent.parent))

from migrations.migrate_employer_profile_data import migrate  # noqa: E402

NAMES = ["Pierre", "Al-Amin", "Adriana", "Rickard", "Other"]


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'ep.db'}")
    with eng.begin() as c:
        c.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, custom_rates JSON)")
        c.exec_driver_sql("CREATE TABLE rate_history (id INTEGER PRIMARY KEY, user_id INTEGER, rates JSON)")
        c.exec_driver_sql(
            "CREATE TABLE day_pay_overrides"
            " (id INTEGER PRIMARY KEY, user_id INTEGER, date DATE, oncall_hours_override JSON)"
        )
        for i, name in enumerate(NAMES, 1):
            rates = {"oncall": {"x": 1}, "ob1": 50}
            c.execute(
                text("INSERT INTO users VALUES (:i, :n, :r)"),
                {"i": i, "n": name, "r": json.dumps(rates)},
            )
            c.execute(
                text("INSERT INTO rate_history VALUES (:i, :i, :r)"),
                {"i": i, "r": json.dumps(rates)},
            )
        c.exec_driver_sql(
            "INSERT INTO day_pay_overrides VALUES"
            " (1, 1, '2026-08-22', '{\"OC_WEEKEND_SAT\": 12.0, \"OC_WEEKDAY\": 1.0}')"
        )
        # Same key on another user must stay untouched.
        c.exec_driver_sql("INSERT INTO day_pay_overrides VALUES (2, 5, '2026-08-22', '{\"OC_WEEKEND_SAT\": 3.0}')")
    return eng


def _rates(engine, table):
    with engine.connect() as c:
        rows = c.execute(
            text(
                f"SELECT user_id, rates FROM {table}"
                if table == "rate_history"
                else "SELECT id, custom_rates FROM users"
            )
        )
        return {r[0]: json.loads(r[1]) for r in rows}


def test_migrate_is_idempotent(engine):
    first = migrate(engine)
    assert first == {"override_renamed": 1, "custom_rates_cleaned": 4, "rate_history_cleaned": 4}
    second = migrate(engine)
    assert second == {"override_renamed": 0, "custom_rates_cleaned": 0, "rate_history_cleaned": 0}

    with engine.connect() as c:
        ov = {
            r[0]: json.loads(r[1]) for r in c.execute(text("SELECT id, oncall_hours_override FROM day_pay_overrides"))
        }
    assert ov[1] == {"NX_WEEKEND": 12.0, "OC_WEEKDAY": 1.0}
    assert ov[2] == {"OC_WEEKEND_SAT": 3.0}

    for table in ("users", "rate_history"):
        rates = _rates(engine, table)
        for uid in (1, 2, 3, 4):
            assert rates[uid] == {"ob1": 50}
        assert rates[5] == {"oncall": {"x": 1}, "ob1": 50}


def test_dry_run_writes_nothing(engine):
    assert migrate(engine, dry_run=True)["override_renamed"] == 1
    assert migrate(engine, dry_run=True)["custom_rates_cleaned"] == 4
    assert _rates(engine, "users")[1] == {"oncall": {"x": 1}, "ob1": 50}


def test_null_and_non_dict_values_are_skipped(engine):
    with engine.begin() as c:
        c.exec_driver_sql("UPDATE users SET custom_rates = NULL WHERE id = 1")
        c.exec_driver_sql("UPDATE users SET custom_rates = '[\"oncall\"]' WHERE id = 2")
        c.exec_driver_sql("UPDATE day_pay_overrides SET oncall_hours_override = '\"OC_WEEKEND_SAT\"' WHERE id = 1")
        c.exec_driver_sql("INSERT INTO day_pay_overrides VALUES (3, 1, '2026-08-22', '7')")
        c.exec_driver_sql("INSERT INTO day_pay_overrides VALUES (4, 1, '2026-08-22', NULL)")
    result = migrate(engine)
    assert result == {"override_renamed": 0, "custom_rates_cleaned": 2, "rate_history_cleaned": 4}
    with engine.connect() as c:
        users = dict(c.execute(text("SELECT id, custom_rates FROM users")).all())
    assert users[1] is None
    assert json.loads(users[2]) == ["oncall"]
