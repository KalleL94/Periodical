"""Unit tests for wage and absence-deduction calculations.

Covers the schedule-independent units of app.core.schedule.wages:
- absence deductions (karens budget, sjuklön, VAB/leave/parental)
- partial-day absent-hour math (left_at / arrived_at)
- temporal wage history (add/query/current) and monthly-equivalent wages
"""

import datetime
from types import SimpleNamespace

import pytest

from app.core.schedule.wages import (
    _MONTHLY_HOURS,
    _get_user_id_for_position,
    _get_wage_type,
    add_new_wage,
    calculate_absence_deduction,
    get_absent_hours_for_absence,
    get_absent_hours_from_left_at,
    get_all_user_wages,
    get_current_wage_record,
    get_effective_monthly_wage,
    get_ot_hourly_rate_from_stored_wage,
    get_user_wage,
    get_wage_history,
    update_wage_value,
)
from app.core.utils import get_today
from app.database.database import User, UserRole, WageHistory, WageType


def _make_user(db, **kwargs):
    defaults = dict(
        username="wageuser",
        password_hash="x",
        name="Wage User",
        role=UserRole.USER,
        wage=30000,
        vacation={},
        must_change_password=0,
        is_active=True,
        wage_type=WageType.MONTHLY,
    )
    defaults.update(kwargs)
    user = User(**defaults)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


HOURLY_WAGE = 30000 / _MONTHLY_HOURS


class TestCalculateAbsenceDeduction:
    def test_sick_with_karens_budget_distributes_waiting_then_sjuklon(self):
        # 8h waiting-day budget remaining, 8.5h absent: 8h at full deduction,
        # remaining 0.5h at the 20% sjuklön deduction.
        result = calculate_absence_deduction(30000, "SICK", 8.5, absent_hours=8.5, karens_remaining=8.0)
        expected = HOURLY_WAGE * 8 + HOURLY_WAGE * 0.5 * 0.2
        assert result == pytest.approx(expected)

    def test_sick_first_day_fallback_consumes_full_karens_budget(self):
        # Legacy call path without karens_remaining and no partial hours.
        result = calculate_absence_deduction(30000, "SICK", 8.5, is_first_sick_day=True)
        assert result == pytest.approx(HOURLY_WAGE * 8.0)

    def test_sick_subsequent_day_is_twenty_percent(self):
        result = calculate_absence_deduction(30000, "SICK", 8.5)
        assert result == pytest.approx(HOURLY_WAGE * 8.5 * 0.2)

    def test_vab_is_full_deduction(self):
        assert calculate_absence_deduction(30000, "VAB", 8.5) == pytest.approx(HOURLY_WAGE * 8.5)

    def test_leave_is_full_deduction(self):
        assert calculate_absence_deduction(30000, "LEAVE", 8.5) == pytest.approx(HOURLY_WAGE * 8.5)

    @pytest.mark.parametrize("absence_type", ["OFF", "PARENTAL", "SOMETHING_ELSE"])
    def test_no_deduction_types(self, absence_type):
        assert calculate_absence_deduction(30000, absence_type, 8.5) == 0.0

    def test_partial_absent_hours_override_shift_hours(self):
        # Only 3 absent hours on a VAB day, regardless of the 8.5h shift.
        result = calculate_absence_deduction(30000, "VAB", 8.5, absent_hours=3.0)
        assert result == pytest.approx(HOURLY_WAGE * 3.0)


class TestGetAbsentHoursFromLeftAt:
    def test_leaving_early_counts_missed_tail(self):
        end = datetime.datetime(2026, 1, 1, 22, 0)
        assert get_absent_hours_from_left_at("18:00", end, 8.5) == 4.0

    def test_leaving_at_or_after_end_is_zero(self):
        end = datetime.datetime(2026, 1, 1, 22, 0)
        assert get_absent_hours_from_left_at("23:00", end, 8.5) == 0.0

    def test_missing_end_time_falls_back_to_shift_hours(self):
        assert get_absent_hours_from_left_at("18:00", None, 8.5) == 8.5

    def test_unparseable_left_at_falls_back(self):
        end = datetime.datetime(2026, 1, 1, 22, 0)
        assert get_absent_hours_from_left_at("nonsense", end, 8.5) == 8.5


class TestGetAbsentHoursForAbsence:
    def test_full_day_when_no_partial_markers(self):
        ab = SimpleNamespace(left_at=None, arrived_at=None)
        start = datetime.datetime(2026, 1, 1, 14, 0)
        end = datetime.datetime(2026, 1, 1, 22, 0)
        assert get_absent_hours_for_absence(ab, start, end, 8.0) == 8.0

    def test_combines_late_arrival_and_early_leave(self):
        # Shift 14-22; arrived 15:00 (missed 1h start) and left 21:00 (missed 1h tail).
        ab = SimpleNamespace(left_at="21:00", arrived_at="15:00")
        start = datetime.datetime(2026, 1, 1, 14, 0)
        end = datetime.datetime(2026, 1, 1, 22, 0)
        assert get_absent_hours_for_absence(ab, start, end, 8.0) == 2.0

    def test_result_capped_at_shift_hours(self):
        ab = SimpleNamespace(left_at="20:00", arrived_at="20:00")
        start = datetime.datetime(2026, 1, 1, 14, 0)
        end = datetime.datetime(2026, 1, 1, 22, 0)
        # Overlapping missed windows must not exceed the shift length.
        assert get_absent_hours_for_absence(ab, start, end, 8.0) <= 8.0


class TestGetOtHourlyRate:
    def test_monthly_divides_by_72(self):
        assert get_ot_hourly_rate_from_stored_wage(None, 1, 30000) == pytest.approx(30000 / 72)

    def test_hourly_returns_stored_rate_directly(self, test_db):
        user = _make_user(test_db, wage_type=WageType.HOURLY, wage=200)
        assert get_ot_hourly_rate_from_stored_wage(test_db, user.id, 200) == 200.0


class TestGetWageType:
    def test_no_session_defaults_to_monthly(self):
        assert _get_wage_type(None, 1) == WageType.MONTHLY

    def test_reads_user_wage_type(self, test_db):
        user = _make_user(test_db, wage_type=WageType.HOURLY)
        assert _get_wage_type(test_db, user.id) == WageType.HOURLY


class TestGetUserWage:
    def test_no_session_uses_fallback(self):
        assert get_user_wage(None, 1, fallback=12345) == 12345

    def test_current_wage_from_user_table(self, test_db):
        user = _make_user(test_db, wage=41000)
        assert get_user_wage(test_db, user.id) == 41000

    def test_temporal_wage_from_history(self, test_db):
        user = _make_user(test_db, wage=50000)
        test_db.add_all(
            [
                WageHistory(
                    user_id=user.id,
                    wage=40000,
                    effective_from=datetime.date(2025, 1, 1),
                    effective_to=datetime.date(2025, 12, 31),
                ),
                WageHistory(
                    user_id=user.id,
                    wage=50000,
                    effective_from=datetime.date(2026, 1, 1),
                    effective_to=None,
                ),
            ]
        )
        test_db.commit()
        assert get_user_wage(test_db, user.id, effective_date=datetime.date(2025, 6, 1)) == 40000
        assert get_user_wage(test_db, user.id, effective_date=datetime.date(2026, 6, 1)) == 50000

    def test_temporal_with_no_history_falls_back_to_user_wage(self, test_db):
        user = _make_user(test_db, wage=33000)
        assert get_user_wage(test_db, user.id, effective_date=datetime.date(2020, 1, 1)) == 33000


class TestGetEffectiveMonthlyWage:
    def test_monthly_returns_wage_as_is(self, test_db):
        user = _make_user(test_db, wage=30000, wage_type=WageType.MONTHLY)
        assert get_effective_monthly_wage(test_db, user.id) == 30000

    def test_hourly_scales_to_monthly_equivalent(self, test_db):
        user = _make_user(test_db, wage=200, wage_type=WageType.HOURLY)
        # 200 * 173.33 so that monthly / 173.33 == 200.
        assert get_effective_monthly_wage(test_db, user.id) == int(200 * _MONTHLY_HOURS)


class TestGetUserIdForPosition:
    def test_returns_holder_user_id(self, test_db):
        _make_user(test_db, id=11, person_id=3)
        assert _get_user_id_for_position(test_db, 3) == 11

    def test_legacy_fallback_to_person_id(self, test_db):
        # No user holds position 7 -> legacy user_id == person_id behavior.
        assert _get_user_id_for_position(test_db, 7) == 7


class TestGetAllUserWages:
    def test_mixes_monthly_hourly_and_fallback(self, test_db):
        _make_user(test_db, id=1, username="m", person_id=1, wage=30000, wage_type=WageType.MONTHLY)
        _make_user(test_db, id=2, username="h", person_id=2, wage=200, wage_type=WageType.HOURLY)
        wages = get_all_user_wages(test_db)
        assert wages[1] == 30000
        assert wages[2] == int(200 * _MONTHLY_HOURS)
        # All rotation positions are present, missing ones filled with the default.
        assert set(wages.keys()) >= set(range(1, 11))


class TestWageHistoryCrud:
    def test_add_new_wage_closes_previous_and_updates_snapshot(self, test_db):
        user = _make_user(test_db, wage=30000)
        # First entry (current).
        add_new_wage(test_db, user.id, 30000, datetime.date(2024, 1, 1))
        # Raise effective today so the live snapshot updates.
        add_new_wage(test_db, user.id, 35000, get_today())

        history = get_wage_history(test_db, user.id)
        assert [h["wage"] for h in history] == [35000, 30000]  # newest first
        # Previous entry got an end date; newest is open/current.
        assert history[0]["is_current"] is True
        assert history[1]["is_current"] is False
        test_db.refresh(user)
        assert user.wage == 35000

    def test_future_wage_does_not_update_snapshot(self, test_db):
        user = _make_user(test_db, wage=30000)
        future = get_today() + datetime.timedelta(days=30)
        add_new_wage(test_db, user.id, 99000, future)
        test_db.refresh(user)
        assert user.wage == 30000

    def test_get_current_wage_record(self, test_db):
        user = _make_user(test_db, wage=30000)
        add_new_wage(test_db, user.id, 30000, datetime.date(2024, 1, 1))
        record = get_current_wage_record(test_db, user.id)
        assert record is not None
        assert record.wage == 30000
        assert record.effective_to is None


class TestUpdateWageValue:
    def test_updates_active_record_and_syncs_user_snapshot(self, test_db):
        user = _make_user(test_db, wage=30000)
        rec = add_new_wage(test_db, user.id, 30000, datetime.date(2024, 1, 1))
        from_before, to_before = rec.effective_from, rec.effective_to

        update_wage_value(test_db, rec.id, user.id, 41000)

        test_db.refresh(rec)
        test_db.refresh(user)
        assert rec.wage == 41000
        # Dates are never touched by an edit.
        assert rec.effective_from == from_before
        assert rec.effective_to == to_before
        # Active record keeps User.wage in sync.
        assert user.wage == 41000

    def test_historical_record_edit_does_not_touch_user_snapshot(self, test_db):
        user = _make_user(test_db, wage=30000)
        old = add_new_wage(test_db, user.id, 30000, datetime.date(2024, 1, 1))
        add_new_wage(test_db, user.id, 35000, datetime.date(2025, 1, 1))
        test_db.refresh(user)
        assert user.wage == 35000

        # Editing the now-closed historical record must not move the snapshot.
        update_wage_value(test_db, old.id, user.id, 31000)
        test_db.refresh(old)
        test_db.refresh(user)
        assert old.wage == 31000
        assert old.effective_to is not None
        assert user.wage == 35000

    def test_wrong_owner_raises_permission_error(self, test_db):
        user = _make_user(test_db, wage=30000)
        other = _make_user(test_db, username="other", person_id=99)
        rec = add_new_wage(test_db, user.id, 30000, datetime.date(2024, 1, 1))
        with pytest.raises(PermissionError):
            update_wage_value(test_db, rec.id, other.id, 50000)

    def test_missing_record_raises_lookup_error(self, test_db):
        user = _make_user(test_db, wage=30000)
        with pytest.raises(LookupError):
            update_wage_value(test_db, 999999, user.id, 50000)
