"""Unit tests for app.core.rates.

Covers both the pure resolution helpers and the temporal RateHistory CRUD,
which mirrors the wage-history pattern: a current snapshot on User.custom_rates
plus an effective_from/effective_to audit trail.
"""

import datetime

import pytest

from app.core.rates import (
    DEFAULT_ONCALL_RATES,
    DEFAULT_VACATION_RATES,
    _resolve_rates,
    add_new_rates,
    delete_rate_history,
    get_all_defaults,
    get_rate_history,
    get_user_rates,
)
from app.core.utils import get_today
from app.database.database import RateHistory, User, UserRole


def _make_user(db, custom_rates=None, employment_start_date=None):
    user = User(
        username="rateuser",
        password_hash="x",
        name="Rate User",
        role=UserRole.USER,
        wage=30000,
        vacation={},
        must_change_password=0,
        custom_rates=custom_rates,
        employment_start_date=employment_start_date,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


class TestResolveRates:
    def test_empty_custom_fills_all_defaults(self):
        r = _resolve_rates({})
        assert r["ob"] == {}
        assert r["ot"] is None
        assert r["oncall"] == DEFAULT_ONCALL_RATES
        assert r["vacation"] == DEFAULT_VACATION_RATES
        assert r["sick"] == {"ob_compensation": False}

    def test_overrides_win_per_key_but_keep_other_defaults(self):
        r = _resolve_rates({"oncall": {"OC_WEEKDAY": 80}})
        assert r["oncall"]["OC_WEEKDAY"] == 80
        # Unspecified on-call keys still fall back to the system default.
        assert r["oncall"]["OC_WEEKEND"] == DEFAULT_ONCALL_RATES["OC_WEEKEND"]

    def test_sick_ob_compensation_coerced_to_bool(self):
        assert _resolve_rates({"sick": {"ob_compensation": 1}})["sick"]["ob_compensation"] is True
        assert _resolve_rates({"sick": {}})["sick"]["ob_compensation"] is False

    def test_ot_and_ob_passthrough(self):
        r = _resolve_rates({"ot": 72, "ob": {"OB1": 500}})
        assert r["ot"] == 72
        assert r["ob"] == {"OB1": 500}


class TestGetAllDefaults:
    def test_returns_copies_not_references(self):
        defaults = get_all_defaults()
        defaults["oncall"]["OC_WEEKDAY"] = 999
        # Mutating the returned dict must not corrupt the module-level default.
        assert DEFAULT_ONCALL_RATES["OC_WEEKDAY"] != 999

    def test_contains_expected_sections(self):
        assert set(get_all_defaults()) == {"ob_divisors", "ot_divisor", "oncall", "vacation"}


class TestGetUserRates:
    def test_falls_back_to_custom_rates_without_date(self, test_db):
        user = _make_user(test_db, custom_rates={"ot": 60})
        assert get_user_rates(user)["ot"] == 60

    def test_none_custom_rates_resolves_to_defaults(self, test_db):
        user = _make_user(test_db, custom_rates=None)
        assert get_user_rates(user)["oncall"] == DEFAULT_ONCALL_RATES

    def test_temporal_query_returns_record_effective_on_date(self, test_db):
        user = _make_user(test_db)
        test_db.add(
            RateHistory(
                user_id=user.id,
                rates={"ot": 50},
                effective_from=datetime.date(2025, 1, 1),
                effective_to=datetime.date(2025, 12, 31),
            )
        )
        test_db.add(
            RateHistory(
                user_id=user.id,
                rates={"ot": 70},
                effective_from=datetime.date(2026, 1, 1),
                effective_to=None,
            )
        )
        test_db.commit()
        assert get_user_rates(user, test_db, datetime.date(2025, 6, 1))["ot"] == 50
        assert get_user_rates(user, test_db, datetime.date(2026, 6, 1))["ot"] == 70

    def test_temporal_query_with_no_record_falls_back_to_custom(self, test_db):
        user = _make_user(test_db, custom_rates={"ot": 99})
        # No RateHistory rows exist, so it falls back to the User snapshot.
        assert get_user_rates(user, test_db, datetime.date(2026, 6, 1))["ot"] == 99


class TestAddNewRates:
    def test_seeds_initial_history_from_existing_custom_rates(self, test_db):
        user = _make_user(
            test_db,
            custom_rates={"ot": 55},
            employment_start_date=datetime.date(2024, 1, 1),
        )
        new_from = get_today() + datetime.timedelta(days=30)
        add_new_rates(test_db, user.id, {"ot": 65}, new_from)

        history = test_db.query(RateHistory).filter(RateHistory.user_id == user.id).all()
        # One seeded entry (old custom_rates) + one new future entry.
        assert len(history) == 2
        seed = next(h for h in history if h.rates == {"ot": 55})
        assert seed.effective_from == datetime.date(2024, 1, 1)
        # The seed got closed the day before the new entry starts.
        assert seed.effective_to == new_from - datetime.timedelta(days=1)

    def test_future_rate_does_not_update_current_snapshot(self, test_db):
        user = _make_user(test_db, custom_rates={"ot": 40})
        future = get_today() + datetime.timedelta(days=10)
        add_new_rates(test_db, user.id, {"ot": 90}, future)
        test_db.refresh(user)
        # Future-dated rate must not overwrite the live snapshot yet.
        assert user.custom_rates == {"ot": 40}

    def test_current_rate_updates_snapshot(self, test_db):
        user = _make_user(test_db, custom_rates={"ot": 40})
        add_new_rates(test_db, user.id, {"ot": 100}, get_today())
        test_db.refresh(user)
        assert user.custom_rates == {"ot": 100}


class TestGetRateHistory:
    def test_status_classification(self, test_db):
        user = _make_user(test_db)
        today = get_today()
        test_db.add_all(
            [
                RateHistory(
                    user_id=user.id,
                    rates={"ot": 1},
                    effective_from=today - datetime.timedelta(days=400),
                    effective_to=today - datetime.timedelta(days=200),
                ),
                RateHistory(
                    user_id=user.id,
                    rates={"ot": 2},
                    effective_from=today - datetime.timedelta(days=199),
                    effective_to=None,
                ),
                RateHistory(
                    user_id=user.id,
                    rates={"ot": 3},
                    effective_from=today + datetime.timedelta(days=30),
                    effective_to=None,
                ),
            ]
        )
        test_db.commit()
        history = get_rate_history(test_db, user.id)
        status_by_ot = {h["rates"]["ot"]: h["status"] for h in history}
        assert status_by_ot == {1: "historical", 2: "current", 3: "future"}
        # Newest first ordering by effective_from.
        assert [h["rates"]["ot"] for h in history] == [3, 2, 1]


class TestDeleteRateHistory:
    def test_delete_reopens_previous_record(self, test_db):
        user = _make_user(test_db)
        prev = RateHistory(
            user_id=user.id,
            rates={"ot": 10},
            effective_from=datetime.date(2025, 1, 1),
            effective_to=datetime.date(2025, 12, 31),
        )
        latest = RateHistory(
            user_id=user.id,
            rates={"ot": 20},
            effective_from=datetime.date(2026, 1, 1),
            effective_to=None,
        )
        test_db.add_all([prev, latest])
        test_db.commit()

        delete_rate_history(test_db, latest.id, user.id)
        test_db.refresh(prev)
        # Deleting the open latest entry reopens the one it had closed.
        assert prev.effective_to is None
        assert test_db.query(RateHistory).filter(RateHistory.user_id == user.id).count() == 1

    def test_delete_does_not_reopen_when_a_later_record_remains(self, test_db):
        user = _make_user(test_db)
        first = RateHistory(
            user_id=user.id,
            rates={"ot": 10},
            effective_from=datetime.date(2025, 1, 1),
            effective_to=datetime.date(2025, 6, 30),
        )
        middle = RateHistory(
            user_id=user.id,
            rates={"ot": 20},
            effective_from=datetime.date(2025, 7, 1),
            effective_to=datetime.date(2025, 12, 31),
        )
        last = RateHistory(
            user_id=user.id,
            rates={"ot": 30},
            effective_from=datetime.date(2026, 1, 1),
            effective_to=None,
        )
        test_db.add_all([first, middle, last])
        test_db.commit()

        # Delete the middle entry: 'first' should stay closed because 'last' is later.
        delete_rate_history(test_db, middle.id, user.id)
        test_db.refresh(first)
        assert first.effective_to == datetime.date(2025, 6, 30)

    def test_delete_missing_record_is_noop(self, test_db):
        user = _make_user(test_db)
        # Should not raise even when the id does not exist.
        delete_rate_history(test_db, 9999, user.id)


@pytest.mark.parametrize("invalid", [None, {}])
def test_resolve_rates_handles_falsy_sections(invalid):
    # ob/oncall/vacation given as None or {} must still resolve cleanly.
    r = _resolve_rates({"ob": invalid, "oncall": invalid, "vacation": invalid})
    assert r["oncall"] == DEFAULT_ONCALL_RATES
    assert r["vacation"] == DEFAULT_VACATION_RATES
