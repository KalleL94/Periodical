"""Unit tests for vacation balance and pay calculations (Handelns avtal §9).

Focus on the money-critical, schedule-independent units:
- vacation-year boundaries and proration (§9 punkt 2)
- counting consumed days from week-based and day-level sources
- saved-day balances (semesterlag §18, 5-year validity)
- closing a year (semesterersättning §9.5)
- merging week-based + absence vacation dates

The full schedule-dependent orchestration (calculate_vacation_balance,
_scheduled_off_dates) is exercised elsewhere; here we inject a session so no
rotation era is required.
"""

import datetime
from types import SimpleNamespace

from app.core.schedule.vacation import (
    _calculate_prorated_days,
    _count_weekdays_in_vacation_weeks,
    calculate_vacation_balance,
    calculate_vacation_pay,
    close_vacation_year,
    count_vacation_days_used,
    get_parental_dates_for_year,
    get_saved_days_balance,
    get_vacation_dates_for_year,
    get_vacation_year_boundaries,
)
from app.database.database import Absence, AbsenceType, User, UserRole


def _make_user(db, **kwargs):
    defaults = dict(
        username="vacuser",
        password_hash="x",
        name="Vac User",
        role=UserRole.USER,
        wage=30000,
        vacation={},
        must_change_password=0,
        is_active=True,
    )
    defaults.update(kwargs)
    user = User(**defaults)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


class TestVacationYearBoundaries:
    def test_april_break_spans_two_calendar_years(self):
        start, end = get_vacation_year_boundaries(2026, 4)
        assert start == datetime.date(2026, 4, 1)
        assert end == datetime.date(2027, 3, 31)

    def test_january_break_is_a_single_calendar_year(self):
        start, end = get_vacation_year_boundaries(2026, 1)
        assert start == datetime.date(2026, 1, 1)
        assert end == datetime.date(2026, 12, 31)


class TestCountWeekdaysInVacationWeeks:
    def test_without_off_dates_counts_mon_to_fri(self):
        ps, pe = datetime.date(2026, 1, 1), datetime.date(2026, 12, 31)
        # Two full weeks, Mon-Fri each.
        assert _count_weekdays_in_vacation_weeks([10, 11], 2026, ps, pe) == 10

    def test_invalid_week_number_is_skipped(self):
        ps, pe = datetime.date(2026, 1, 1), datetime.date(2026, 12, 31)
        assert _count_weekdays_in_vacation_weeks([99], 2026, ps, pe) == 0

    def test_with_off_dates_uses_all_seven_days_minus_off(self):
        ps, pe = datetime.date(2026, 1, 1), datetime.date(2026, 12, 31)
        off = {
            datetime.date.fromisocalendar(2026, 10, 6),  # Sat
            datetime.date.fromisocalendar(2026, 10, 7),  # Sun
        }
        # 7 scheduled days minus 2 OFF days = 5 consumed.
        assert _count_weekdays_in_vacation_weeks([10], 2026, ps, pe, off) == 5

    def test_days_outside_period_are_excluded(self):
        # Period ends mid-week; only the in-window weekdays count.
        ps = datetime.date(2026, 1, 1)
        pe = datetime.date.fromisocalendar(2026, 10, 2)  # Tuesday of week 10
        assert _count_weekdays_in_vacation_weeks([10], 2026, ps, pe) == 2


class TestCalculateProratedDays:
    def test_full_year_employment_via_half_year(self):
        # Started 2025-10-01 within the 2025-04 -> 2026-03 earning year.
        result = _calculate_prorated_days(
            datetime.date(2025, 10, 1),
            datetime.date(2025, 4, 1),
            datetime.date(2026, 3, 31),
            25,
        )
        # 182 employment days / 365 total, ceil(25 * 182 / 365) = 13.
        assert result == 13

    def test_start_after_earning_year_yields_zero(self):
        assert (
            _calculate_prorated_days(
                datetime.date(2027, 1, 1),
                datetime.date(2025, 4, 1),
                datetime.date(2026, 3, 31),
                25,
            )
            == 0
        )

    def test_start_before_earning_year_gives_full_entitlement(self):
        # Employment predates the earning year -> clamped to full year -> all 25.
        assert (
            _calculate_prorated_days(
                datetime.date(2020, 1, 1),
                datetime.date(2025, 4, 1),
                datetime.date(2026, 3, 31),
                25,
            )
            == 25
        )


class TestGetSavedDaysBalance:
    def test_sums_only_last_five_years_with_positive_saved(self):
        user = SimpleNamespace(
            vacation_saved={
                "2019": {"saved": 9},  # older than 5 years -> excluded
                "2022": {"saved": 3},
                "2024": {"saved": 5},
                "2025": {"saved": 0},  # zero -> excluded
            }
        )
        result = get_saved_days_balance(user, 2026)
        assert result["total_saved"] == 8
        assert result["breakdown"] == [
            {"year": "2022", "days": 3},
            {"year": "2024", "days": 5},
        ]

    def test_no_saved_days_returns_empty(self):
        user = SimpleNamespace(vacation_saved=None)
        assert get_saved_days_balance(user, 2026) == {"total_saved": 0, "breakdown": []}


class TestCountVacationDaysUsed:
    def test_combines_week_based_and_day_level(self, test_db):
        user = _make_user(test_db, person_id=1, vacation={"2026": [10]})
        # One day-level VACATION absence outside week 10.
        test_db.add(
            Absence(
                user_id=user.id,
                date=datetime.date(2026, 6, 1),
                absence_type=AbsenceType.VACATION,
            )
        )
        test_db.commit()
        result = count_vacation_days_used(
            user_id=user.id,
            year_start=datetime.date(2026, 1, 1),
            year_end=datetime.date(2026, 12, 31),
            db=test_db,
            vacation_json=user.vacation,
        )
        assert result["week_based"] == 5  # week 10, Mon-Fri
        assert result["day_level"] == 1
        assert result["total"] == 6

    def test_loads_vacation_json_from_db_when_not_passed(self, test_db):
        user = _make_user(test_db, person_id=2, vacation={"2026": [11]})
        result = count_vacation_days_used(
            user_id=user.id,
            year_start=datetime.date(2026, 1, 1),
            year_end=datetime.date(2026, 12, 31),
            db=test_db,
        )
        assert result["week_based"] == 5
        assert result["total"] == 5


class TestCloseVacationYear:
    def test_saves_up_to_five_and_pays_out_rest(self, test_db):
        user = _make_user(test_db, person_id=3, vacation_saved={})
        pay = {"monthly_salary": 30000, "supplement_per_day": 100, "payout_pct": 0.046}
        # 8 remaining -> save 5, pay out 3.
        result = close_vacation_year(user, 2025, remaining_own=8, pay=pay, db=test_db)
        assert result["saved"] == 5
        assert result["paid_out"] == 3
        # payout_per_day = 30000 * 0.046 + 100 = 1480.
        assert result["payout_per_day"] == 1480.0
        assert result["payout_amount"] == round(3 * 1480.0, 2)
        # Persisted onto the user.
        test_db.refresh(user)
        assert user.vacation_saved["2025"]["saved"] == 5

    def test_negative_remaining_saves_nothing(self, test_db):
        user = _make_user(test_db, person_id=4, vacation_saved={})
        pay = {"monthly_salary": 30000, "supplement_per_day": 0, "payout_pct": 0.046}
        result = close_vacation_year(user, 2025, remaining_own=-2, pay=pay, db=test_db)
        assert result["saved"] == 0
        assert result["paid_out"] == 0
        assert result["payout_amount"] == 0


class TestGetVacationDatesForYear:
    def test_merges_week_based_and_absences_and_skips_invalid_week(self, test_db):
        user = _make_user(test_db, person_id=1, vacation={"2026": [10, 99]})
        test_db.add(
            Absence(
                user_id=user.id,
                date=datetime.date(2026, 8, 15),
                absence_type=AbsenceType.VACATION,
            )
        )
        test_db.commit()
        per_person = get_vacation_dates_for_year(2026, session=test_db)
        dates = per_person[1]
        # Week 10 Monday is present, the invalid week 99 contributed nothing.
        assert datetime.date.fromisocalendar(2026, 10, 1) in dates
        # The day-level absence is merged in.
        assert datetime.date(2026, 8, 15) in dates
        # Week 10 has 7 days + 1 absence day = 8 dates total.
        assert len(dates) == 8

    def test_inactive_user_excluded(self, test_db):
        _make_user(test_db, person_id=5, is_active=False, vacation={"2026": [12]})
        per_person = get_vacation_dates_for_year(2026, session=test_db)
        assert per_person[5] == set()


class TestGetParentalDatesForYear:
    def test_merges_week_based_and_absences(self, test_db):
        user = _make_user(test_db, person_id=2, parental_leave={"2026": [20]})
        test_db.add(
            Absence(
                user_id=user.id,
                date=datetime.date(2026, 9, 3),
                absence_type=AbsenceType.PARENTAL,
            )
        )
        test_db.commit()
        per_person = get_parental_dates_for_year(2026, session=test_db)
        dates = per_person[2]
        assert datetime.date.fromisocalendar(2026, 20, 1) in dates
        assert datetime.date(2026, 9, 3) in dates
        assert len(dates) == 8  # 7 days of week 20 + 1 absence day


class TestCalculateVacationBalanceIntegration:
    """End-to-end §9 pipeline against a real rotation era (rotation_session fixture)."""

    def test_open_year_returns_projection_not_closed(self, rotation_session):
        user = rotation_session.query(User).filter(User.id == 1).first()
        user.vacation_year_start_month = 4
        user.vacation_days_per_year = 25
        user.employment_start_date = datetime.date(2020, 1, 1)
        user.vacation = {}
        user.vacation_saved = {}
        rotation_session.commit()

        # 2026 vacation year (Apr 2026 - Mar 2027) is still open as of the test clock.
        balance = calculate_vacation_balance(user, 2026, rotation_session)

        assert balance["entitled_days"] == 25
        assert balance["is_first_year"] is False
        assert balance["used_days"] == 0
        assert balance["remaining_days"] == 25
        assert balance["year_start"] == datetime.date(2026, 4, 1)
        assert balance["year_end"] == datetime.date(2027, 3, 31)
        # Open year: a projection is produced and the year is not yet closed.
        assert balance["closed"] is None
        assert balance["projection"] is not None
        assert balance["projection"]["days_to_save"] == 5
        assert balance["projection"]["days_to_pay_out"] == 20
        assert "supplement_per_day" in balance["pay"]

    def test_past_year_is_auto_closed(self, rotation_session):
        user = rotation_session.query(User).filter(User.id == 1).first()
        user.vacation_year_start_month = 4
        user.vacation_days_per_year = 25
        user.employment_start_date = datetime.date(2020, 1, 1)
        user.vacation = {}
        user.vacation_saved = {}
        rotation_session.commit()

        # 2023 vacation year ended long ago -> lazy auto-close on first access.
        balance = calculate_vacation_balance(user, 2023, rotation_session)

        assert balance["projection"] is None
        assert balance["closed"] is not None
        # 25 entitled, none used -> save 5, pay out 20.
        assert balance["closed"]["saved"] == 5
        assert balance["closed"]["paid_out"] == 20
        # Persisted onto the user for future lookups.
        rotation_session.refresh(user)
        assert user.vacation_saved["2023"]["saved"] == 5


class TestCalculateVacationPay:
    def test_fixed_supplement_without_rotation_position(self, test_db):
        # person_id outside 1..10 means rotation_person_id is out of range, so the
        # variable-earnings loop is skipped and only the fixed part applies.
        user = _make_user(test_db, person_id=99, wage=30000)
        pay = calculate_vacation_pay(
            user=user,
            entitled_days=25,
            earning_start=datetime.date(2025, 4, 1),
            earning_end=datetime.date(2026, 3, 31),
            db=test_db,
            vacation_rates={"fixed_pct": 0.008, "variable_pct": 0.005, "payout_pct": 0.046},
        )
        # Fixed part = 30000 * 0.008 = 240, no variable earnings.
        assert pay["fixed_per_day"] == 240.0
        assert pay["variable_per_day"] == 0.0
        assert pay["supplement_per_day"] == 240.0
        assert pay["supplement_total"] == round(240.0 * 25, 2)
        assert pay["payout_pct"] == 0.046
