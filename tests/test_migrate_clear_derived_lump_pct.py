"""Checks for migrations/migrate_clear_derived_lump_pct.py.

The field used to be computed and never read, and the per-year settings migration
copied it into every entry it created. It is read now, so those copies would turn a
stale artefact into a stated intent: an absent value derives the statutory 0.5% per
paid day, a present one is taken at its word and pays a part year differently.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.schedule.vacation import vacation_settings_for_year
from app.database.database import User, UserRole
from migrations.migrate_clear_derived_lump_pct import clear


def _user(db, uid, settings):
    user = User(
        id=uid,
        username=f"user{uid}",
        password_hash="x",
        name=f"User {uid}",
        role=UserRole.USER,
        wage=30000,
        vacation={},
        must_change_password=0,
        vacation_settings=settings,
    )
    db.add(user)
    db.commit()
    return user


def test_the_field_is_stripped_and_the_rest_of_the_entry_survives(test_db):
    user = _user(
        test_db,
        1,
        {
            "2026": {"payout_rule": "procent", "variable_lump_pct": 0.065},
            "consultant": {"variable_payout": "lump", "variable_lump_pct": 0.125},
        },
    )

    assert clear(test_db) == 1

    test_db.refresh(user)
    assert user.vacation_settings == {
        "2026": {"payout_rule": "procent"},
        "consultant": {"variable_payout": "lump"},
    }
    assert vacation_settings_for_year(user, 2026)["variable_lump_pct"] is None


def test_a_user_without_the_field_is_left_alone_and_the_run_is_idempotent(test_db):
    untouched = _user(test_db, 1, {"2026": {"payout_rule": "procent"}})
    stale = _user(test_db, 2, {"2026": {"variable_lump_pct": 0.12}})

    assert clear(test_db) == 1  # only the stale one counts
    assert clear(test_db) == 0  # nothing left to do

    test_db.refresh(untouched)
    test_db.refresh(stale)
    assert untouched.vacation_settings == {"2026": {"payout_rule": "procent"}}
    assert stale.vacation_settings == {"2026": {}}


def test_a_dry_run_writes_nothing(test_db):
    user = _user(test_db, 1, {"2026": {"variable_lump_pct": 0.12}})

    assert clear(test_db, dry_run=True) == 1

    test_db.refresh(user)
    assert user.vacation_settings == {"2026": {"variable_lump_pct": 0.12}}
