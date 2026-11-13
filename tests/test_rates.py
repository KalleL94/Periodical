"""Unit tests for app.core.rates.

Covers both the pure resolution helpers and the temporal RateHistory CRUD,
which mirrors the wage-history pattern: a current snapshot on User.custom_rates
plus an effective_from/effective_to audit trail.
"""

import datetime

import pytest

from app.core.oncall import ONCALL_GROUP_BY_CODE
from app.core.rates import (
    DEFAULT_VACATION_RATES,
    _resolve_rates,
    add_new_rates,
    delete_rate_history,
    get_all_defaults,
    get_rate_history,
    get_user_rates,
    profile_oncall_rates,
    update_rate_history_value,
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
        assert r["oncall"] == profile_oncall_rates("ica", get_today())
        assert r["vacation"] == DEFAULT_VACATION_RATES
        assert r["sick"] == {"ob_compensation": False}

    def test_overrides_win_per_key_but_keep_other_defaults(self):
        r = _resolve_rates({"oncall": {"OC_WEEKDAY": 80}})
        assert r["oncall"]["OC_WEEKDAY"] == 80
        # Unspecified on-call keys still fall back to the system default.
        assert r["oncall"]["OC_WEEKEND"] == profile_oncall_rates("ica", get_today())["OC_WEEKEND"]

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
        assert get_all_defaults()["oncall"]["OC_WEEKDAY"] != 999

    def test_contains_expected_sections(self):
        assert set(get_all_defaults()) == {"ob_divisors", "ot_divisor", "oncall", "vacation"}


class TestGetUserRates:
    def test_falls_back_to_custom_rates_without_date(self, test_db):
        user = _make_user(test_db, custom_rates={"ot": 60})
        assert get_user_rates(user)["ot"] == 60

    def test_none_custom_rates_resolves_to_defaults(self, test_db):
        user = _make_user(test_db, custom_rates=None)
        assert get_user_rates(user)["oncall"] == profile_oncall_rates("ica", get_today())

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

    def test_boundary_day_returns_old_rate_not_bumped_snapshot(self, test_db):
        """Regression test mirroring the wage boundary fix in PR #286.

        add_new_rates() closes the previous record with
        effective_to = new_effective_from - 1 day, meant as the INCLUSIVE last
        valid day of the old rates. Querying get_user_rates() for that exact
        boundary day must still return the OLD rates via RateHistory, not fall
        through to User.custom_rates -- which add_new_rates() has already bumped
        to the new value because the change is effective today.
        """
        user = _make_user(test_db, custom_rates={"ot": 40})
        today = get_today()
        boundary_day = today - datetime.timedelta(days=1)

        add_new_rates(test_db, user.id, {"ot": 100}, today)
        test_db.refresh(user)
        # Snapshot already bumped since the new rate is effective today.
        assert user.custom_rates == {"ot": 100}

        # The day before must still resolve to the OLD rate through RateHistory.
        assert get_user_rates(user, test_db, boundary_day)["ot"] == 40


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

    def test_a_back_dated_rate_slots_in_behind_the_later_one(self, test_db):
        """Back-dating used to close the open record against the earlier date, so the
        later record ended before it began and every later date fell back to the
        back-dated rate."""
        user = _make_user(test_db)
        add_new_rates(test_db, user.id, {"ot": 200}, datetime.date(2026, 10, 1))
        add_new_rates(test_db, user.id, {"ot": 100}, datetime.date(2025, 10, 1))

        rows = test_db.query(RateHistory).filter(RateHistory.user_id == user.id).order_by(RateHistory.effective_from)
        assert [(r.rates["ot"], r.effective_from, r.effective_to) for r in rows] == [
            (100, datetime.date(2025, 10, 1), datetime.date(2026, 9, 30)),
            (200, datetime.date(2026, 10, 1), None),
        ]
        assert get_user_rates(user, session=test_db, effective_date=datetime.date(2026, 9, 30))["ot"] == 100
        assert get_user_rates(user, session=test_db, effective_date=datetime.date(2027, 6, 1))["ot"] == 200

    def test_a_rate_inserted_between_two_records_closes_the_earlier_one(self, test_db):
        user = _make_user(test_db)
        add_new_rates(test_db, user.id, {"ot": 100}, datetime.date(2024, 1, 1))
        add_new_rates(test_db, user.id, {"ot": 300}, datetime.date(2026, 1, 1))
        add_new_rates(test_db, user.id, {"ot": 200}, datetime.date(2025, 1, 1))

        rows = test_db.query(RateHistory).filter(RateHistory.user_id == user.id).order_by(RateHistory.effective_from)
        assert [(r.rates["ot"], r.effective_to) for r in rows] == [
            (100, datetime.date(2024, 12, 31)),
            (200, datetime.date(2025, 12, 31)),
            (300, None),
        ]

    def test_a_back_dated_rate_does_not_become_todays_snapshot(self, test_db):
        user = _make_user(test_db, custom_rates={"ot": 40})
        add_new_rates(test_db, user.id, {"ot": 200}, get_today() - datetime.timedelta(days=1))
        add_new_rates(test_db, user.id, {"ot": 100}, datetime.date(2021, 1, 1))
        test_db.refresh(user)
        assert user.custom_rates == {"ot": 200}

    def test_same_effective_from_replaces_the_existing_record(self, test_db):
        user = _make_user(test_db)
        add_new_rates(test_db, user.id, {"ot": 100}, datetime.date(2024, 1, 1))
        add_new_rates(test_db, user.id, {"ot": 150}, datetime.date(2024, 1, 1))

        rows = test_db.query(RateHistory).filter(RateHistory.user_id == user.id).all()
        assert [(r.rates, r.effective_to) for r in rows] == [({"ot": 150}, None)]
        test_db.refresh(user)
        assert user.custom_rates == {"ot": 150}


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


class TestUpdateRateHistoryValue:
    def test_updates_active_record_and_syncs_user_snapshot(self, test_db):
        user = _make_user(test_db, custom_rates={"ot": 50})
        rec = RateHistory(
            user_id=user.id,
            rates={"ot": 50},
            effective_from=datetime.date(2024, 1, 1),
            effective_to=None,
        )
        test_db.add(rec)
        test_db.commit()

        update_rate_history_value(test_db, rec.id, user.id, {"ot": 75})

        test_db.refresh(rec)
        test_db.refresh(user)
        assert rec.rates == {"ot": 75}
        # Dates untouched.
        assert rec.effective_from == datetime.date(2024, 1, 1)
        assert rec.effective_to is None
        # Active record syncs the snapshot.
        assert user.custom_rates == {"ot": 75}

    def test_historical_record_edit_does_not_touch_user_snapshot(self, test_db):
        user = _make_user(test_db, custom_rates={"ot": 20})
        old = RateHistory(
            user_id=user.id,
            rates={"ot": 10},
            effective_from=datetime.date(2025, 1, 1),
            effective_to=datetime.date(2025, 12, 31),
        )
        test_db.add(old)
        test_db.commit()

        update_rate_history_value(test_db, old.id, user.id, {"ot": 11})
        test_db.refresh(old)
        test_db.refresh(user)
        assert old.rates == {"ot": 11}
        assert user.custom_rates == {"ot": 20}

    def test_wrong_owner_raises_permission_error(self, test_db):
        user = _make_user(test_db)
        other = User(
            username="otheruser",
            password_hash="x",
            name="Other",
            role=UserRole.USER,
            wage=30000,
            vacation={},
            must_change_password=0,
        )
        test_db.add(other)
        test_db.commit()
        test_db.refresh(other)
        rec = RateHistory(
            user_id=user.id,
            rates={"ot": 10},
            effective_from=datetime.date(2024, 1, 1),
            effective_to=None,
        )
        test_db.add(rec)
        test_db.commit()
        with pytest.raises(PermissionError):
            update_rate_history_value(test_db, rec.id, other.id, {"ot": 99})

    def test_missing_record_raises_lookup_error(self, test_db):
        user = _make_user(test_db)
        with pytest.raises(LookupError):
            update_rate_history_value(test_db, 999999, user.id, {"ot": 99})


@pytest.mark.parametrize("invalid", [None, {}])
def test_resolve_rates_handles_falsy_sections(invalid):
    # ob/oncall/vacation given as None or {} must still resolve cleanly.
    r = _resolve_rates({"ob": invalid, "oncall": invalid, "vacation": invalid})
    assert r["oncall"] == profile_oncall_rates("ica", get_today())
    assert r["vacation"] == DEFAULT_VACATION_RATES


def test_ica_oncall_rate_dated():
    assert profile_oncall_rates("ica", datetime.date(2026, 4, 30))["OC_WEEKEND_SAT"] == 97
    assert profile_oncall_rates("ica", datetime.date(2026, 5, 1))["OC_WEEKEND_SAT"] == 100
    assert profile_oncall_rates("ica", datetime.date(2026, 5, 1))["OC_SPECIAL"] == 198
    assert profile_oncall_rates("ica", datetime.date(2026, 5, 1))["OC_NATIONALDAGEN"] == 116


def test_agiremus_keeps_old_rates():
    assert profile_oncall_rates("agiremus", datetime.date(2026, 6, 1))["OC_WEEKDAY"] == 75


def test_experis_keeps_old_rates():
    assert profile_oncall_rates("experis", datetime.date(2026, 6, 1))["OC_WEEKDAY"] == 75
    assert profile_oncall_rates("experis", datetime.date(2026, 6, 1))["OC_SPECIAL"] == 192


def test_nexer_has_no_fixed_rates():
    assert profile_oncall_rates("nexer", datetime.date(2026, 6, 1)) == {}


def test_unknown_profile_falls_back_to_ica():
    assert profile_oncall_rates("bogus", datetime.date(2026, 6, 1)) == profile_oncall_rates(
        "ica", datetime.date(2026, 6, 1)
    )


def test_custom_oncall_rate_still_wins(test_db, test_user):
    test_user.custom_rates = {"oncall": {"OC_WEEKDAY": 50}}
    r = get_user_rates(test_user, effective_date=datetime.date(2026, 6, 1))
    assert r["oncall"]["OC_WEEKDAY"] == 50 and r["oncall"]["OC_WEEKEND_SAT"] == 100 and r["profile"] == "ica"


def test_rate_history_record_gets_profile_rates_for_its_date(test_db):
    user = _make_user(test_db)
    user.employer_profile = "agiremus"
    test_db.add(RateHistory(user_id=user.id, rates={"ot": 70}, effective_from=datetime.date(2026, 1, 1)))
    test_db.commit()
    r = get_user_rates(user, test_db, datetime.date(2026, 6, 1))
    assert r["ot"] == 70 and r["oncall"]["OC_WEEKEND_SAT"] == 97 and r["profile"] == "agiremus"


def test_get_all_defaults_reports_todays_ica_rates():
    assert get_all_defaults()["oncall"] == profile_oncall_rates("ica", get_today())


def test_oncall_group_map_keeps_ica_grouping():
    assert ONCALL_GROUP_BY_CODE["OC_HOLIDAY_EVE"] == "oc_helgdag"
    assert ONCALL_GROUP_BY_CODE["OC_NATIONALDAGEN"] == "oc_helgdag"
    assert ONCALL_GROUP_BY_CODE["OC_WEEKEND_MON"] == "oc_helg"
    assert set(ONCALL_GROUP_BY_CODE.values()) == {"oc_vardag", "oc_helg", "oc_helgdag", "oc_storhelg"}


def test_each_day_uses_the_rates_in_effect_that_day(rotation_session):
    """Callers resolve one rate record at the start of the month and pass it in
    user_rates_map. A rate change on the 15th must still price the 20th."""
    from app.core.schedule import generate_period_data
    from app.database.database import OvertimeShift

    session = rotation_session
    user = session.get(User, 1)
    session.add(
        RateHistory(
            user_id=1,
            rates={"ot": 100},
            effective_from=datetime.date(2026, 1, 1),
            effective_to=datetime.date(2026, 4, 14),
        )
    )
    session.add(RateHistory(user_id=1, rates={"ot": 200}, effective_from=datetime.date(2026, 4, 15)))
    day = datetime.date(2026, 4, 20)
    session.add(
        OvertimeShift(
            user_id=1,
            date=day,
            start_time=datetime.time(18, 0),
            end_time=datetime.time(20, 0),
            hours=2.0,
            ot_pay=0.0,
            kind="ot",
            side="after",
        )
    )
    session.commit()

    month_rates = get_user_rates(user, session=session, effective_date=datetime.date(2026, 4, 1))
    days = generate_period_data(
        datetime.date(2026, 4, 1), datetime.date(2026, 4, 30), 1, session=session, user_rates_map={1: month_rates}
    )
    assert next(d for d in days if d["date"] == day)["ot_pay"] == 400


def test_month_summary_prices_ob_with_the_rates_of_each_day(rotation_session):
    """OB is priced in summary.py from the rates resolved at the month start. A
    record starting mid-month must price the days after it."""
    from app.core.schedule import summarize_month_for_person

    session = rotation_session
    session.add(
        RateHistory(
            user_id=1, rates={}, effective_from=datetime.date(2026, 1, 1), effective_to=datetime.date(2026, 3, 14)
        )
    )
    session.add(RateHistory(user_id=1, rates={"ob": {"OB2": 500}}, effective_from=datetime.date(2026, 3, 15)))
    session.commit()

    days = summarize_month_for_person(2026, 3, 1, session=session, fetch_tax_table=False)["days"]
    # 2026-03-18 is an N3 night with 6.5 OB2 hours: they earn the new rate.
    night = next(d for d in days if d["date"] == datetime.date(2026, 3, 18))
    assert night["ob_hours"]["OB2"] == 6.5
    assert night["ob_pay"]["OB2"] == 6.5 * 500
