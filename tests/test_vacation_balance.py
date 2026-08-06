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
    _vacation_dates_in_weeks,
    calculate_vacation_balance,
    calculate_vacation_pay,
    close_vacation_year,
    count_vacation_days_used,
    get_parental_dates_for_year,
    get_saved_days_balance,
    get_vacation_dates_for_year,
    get_vacation_year_boundaries,
    resolve_vacation_year,
    vacation_settings_for_year,
)
from app.core.utils import get_today
from app.database.database import Absence, AbsenceType, ConsultantSalaryType, User, UserRole


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
        assert len(_vacation_dates_in_weeks([10, 11], 2026, ps, pe)) == 10

    def test_invalid_week_number_is_skipped(self):
        ps, pe = datetime.date(2026, 1, 1), datetime.date(2026, 12, 31)
        assert len(_vacation_dates_in_weeks([99], 2026, ps, pe)) == 0

    def test_with_off_dates_uses_all_seven_days_minus_off(self):
        ps, pe = datetime.date(2026, 1, 1), datetime.date(2026, 12, 31)
        off = {
            datetime.date.fromisocalendar(2026, 10, 6),  # Sat
            datetime.date.fromisocalendar(2026, 10, 7),  # Sun
        }
        # 7 scheduled days minus 2 OFF days = 5 consumed.
        assert len(_vacation_dates_in_weeks([10], 2026, ps, pe, off)) == 5

    def test_days_outside_period_are_excluded(self):
        # Period ends mid-week; only the in-window weekdays count.
        ps = datetime.date(2026, 1, 1)
        pe = datetime.date.fromisocalendar(2026, 10, 2)  # Tuesday of week 10
        assert len(_vacation_dates_in_weeks([10], 2026, ps, pe)) == 2


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

    def test_day_level_on_a_week_already_taken_is_not_charged_twice(self, test_db):
        # The vacation page offers both the week picker and the day calendar, so
        # the same date can be recorded in both. It must cost one day, not two.
        user = _make_user(test_db, person_id=6, vacation={"2026": [10]})
        monday_of_week_10 = datetime.date.fromisocalendar(2026, 10, 1)
        test_db.add(
            Absence(
                user_id=user.id,
                date=monday_of_week_10,
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
        assert result["week_based"] == 5
        assert result["day_level"] == 0
        assert result["total"] == 5

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

    def test_lump_payout_still_owes_the_variable_part_on_unused_days(self, test_db):
        """Under lump payout supplement_per_day carries the fixed part only, but
        semesterersättning (Handelns avtal 9.5) owes the whole supplement on every
        day paid out. Reading the reduced figure here would quietly underpay."""
        user = _make_user(test_db, person_id=6, vacation_saved={})
        pay = {
            "monthly_salary": 30000,
            "supplement_per_day": 240,
            "full_supplement_per_day": 300,
            "payout_pct": 0.046,
        }
        result = close_vacation_year(user, 2025, remaining_own=8, pay=pay, db=test_db)

        # 30000 * 0.046 + 300 (both parts), not + 240.
        assert result["payout_per_day"] == 1680.0

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


class TestVacationSettingsPerYear:
    """Payout settings are versioned per vacation year.

    They used to be single columns on the user, so changing them for one year rewrote
    every other year with them. A year now resolves to its own entry, else the closest
    earlier one, else the old columns, which is what every year had before.
    """

    def test_falls_back_to_the_user_columns_when_no_year_is_configured(self, test_db):
        user = _make_user(
            test_db,
            vacation_settings={},
            vacation_variable_payout="lump",
            vacation_variable_payout_month=7,
            vacation_fixed_per_day=159.10,
        )

        settings = vacation_settings_for_year(user, 2027)

        assert settings["variable_payout"] == "lump"
        assert settings["variable_payout_month"] == 7
        assert settings["fixed_per_day"] == 159.10
        assert settings["payout_rule"] == "sammalone"

    def test_the_years_own_entry_wins_over_the_columns(self, test_db):
        user = _make_user(
            test_db,
            vacation_settings={"2027": {"variable_payout": "per_day", "payout_rule": "procent"}},
            vacation_variable_payout="lump",
        )

        settings = vacation_settings_for_year(user, 2027)

        assert settings["variable_payout"] == "per_day"
        assert settings["payout_rule"] == "procent"

    def test_a_year_without_an_entry_inherits_the_closest_earlier_one(self, test_db):
        user = _make_user(
            test_db,
            vacation_settings={
                "2024": {"variable_payout": "per_day"},
                "2027": {"variable_payout": "lump"},
            },
            vacation_variable_payout="per_day",
        )

        assert vacation_settings_for_year(user, 2025)["variable_payout"] == "per_day"
        assert vacation_settings_for_year(user, 2027)["variable_payout"] == "lump"
        assert vacation_settings_for_year(user, 2030)["variable_payout"] == "lump"

    def test_a_year_before_every_entry_falls_back_to_the_columns(self, test_db):
        user = _make_user(
            test_db,
            vacation_settings={"2027": {"variable_payout": "lump"}},
            vacation_variable_payout="per_day",
        )

        assert vacation_settings_for_year(user, 2020)["variable_payout"] == "per_day"

    def test_a_field_missing_from_the_entry_still_falls_back(self, test_db):
        # Entries are partial: only what the form submitted is stored, so the rest of
        # the year has to resolve rather than come back as None.
        user = _make_user(
            test_db,
            vacation_settings={"2027": {"payout_rule": "procent"}},
            vacation_fixed_per_day=159.10,
        )

        settings = vacation_settings_for_year(user, 2027)

        assert settings["payout_rule"] == "procent"
        assert settings["fixed_per_day"] == 159.10


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

    def test_days_taken_before_a_transition_do_not_charge_the_direct_employment(self, rotation_session):
        # A transition mid vacation year splits it between two employers. Days taken
        # before it came out of the consultant employer's quota and were deducted from
        # its final payout, so charging them here too leaves a negative balance for a
        # year the direct employment earned nothing in.
        from app.database.database import EmploymentTransition

        user = rotation_session.query(User).filter(User.id == 1).first()
        user.vacation_year_start_month = 4
        user.vacation_days_per_year = 25
        user.employment_start_date = datetime.date(2025, 10, 1)
        user.vacation = {}
        user.vacation_saved = {}
        # Four days taken in June 2026, while still a consultant.
        for day in range(15, 19):
            rotation_session.add(
                Absence(user_id=user.id, date=datetime.date(2026, 6, day), absence_type=AbsenceType.VACATION)
            )
        rotation_session.add(
            EmploymentTransition(
                user_id=user.id,
                transition_date=datetime.date(2026, 12, 1),
                consultant_salary_type=ConsultantSalaryType.TRAILING,
                consultant_vacation_days=0.0,
                consultant_supplement_pct=0.0043,
            )
        )
        rotation_session.commit()
        rotation_session.refresh(user)

        balance = calculate_vacation_balance(user, 2026, rotation_session)

        # Earning year 2025/26 predates the transition, so the direct employment earned
        # nothing for this vacation year, and nothing was taken from it either.
        assert balance["entitled_days"] == 0
        assert balance["used_days"] == 0
        assert balance["remaining_days"] == 0

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

    def test_year_before_employment_is_not_closed(self, rotation_session):
        # Every view reaches a balance on a GET, so closing a year the employee was
        # not around for would mint saved days and a payout just from browsing back.
        user = rotation_session.query(User).filter(User.id == 1).first()
        user.vacation_year_start_month = 4
        user.employment_start_date = datetime.date(2024, 6, 1)
        user.vacation = {}
        user.vacation_saved = {}
        rotation_session.commit()

        balance = calculate_vacation_balance(user, 2022, rotation_session)

        assert balance["closed"] is None
        rotation_session.refresh(user)
        assert user.vacation_saved == {}

    def test_missing_employment_start_date_never_closes(self, rotation_session):
        # Without a start date the entitlement falls back to the full quota, so an
        # unguarded close would hand out five saved days per year browsed.
        user = rotation_session.query(User).filter(User.id == 1).first()
        user.vacation_year_start_month = 4
        user.employment_start_date = None
        user.vacation = {}
        user.vacation_saved = {}
        rotation_session.commit()

        for year in (2021, 2022, 2023):
            assert calculate_vacation_balance(user, year, rotation_session)["closed"] is None

        rotation_session.refresh(user)
        assert user.vacation_saved == {}
        assert calculate_vacation_balance(user, 2026, rotation_session)["saved_from_previous"] == 0


class TestResolveVacationYear:
    def test_out_of_range_year_falls_back_to_current(self):
        # datetime.date raises on a year like 99999, and a balance is expensive to
        # compute, so a query parameter is clamped before it reaches either.
        assert resolve_vacation_year(99999) == get_today().year
        assert resolve_vacation_year(1900) == get_today().year
        assert resolve_vacation_year(None) == get_today().year

    def test_valid_year_is_kept(self):
        assert resolve_vacation_year(2026) == 2026


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

    def test_fixed_per_day_overrides_the_percentage(self, test_db):
        """Some employers pay a flat krona amount per vacation day.

        The flat amount is a User setting, not a rate: the rates are versioned
        through RateHistory and a wage revision must not carry a payout routine
        into a new rate period with it.
        """
        user = _make_user(test_db, person_id=99, wage=30000)
        user.vacation_fixed_per_day = 300.0
        test_db.commit()

        pay = calculate_vacation_pay(
            user=user,
            entitled_days=25,
            earning_start=datetime.date(2025, 4, 1),
            earning_end=datetime.date(2026, 3, 31),
            db=test_db,
            vacation_rates={"fixed_pct": 0.008, "variable_pct": 0.005, "payout_pct": 0.046},
        )
        # 300 flat, not 30000 * 0.008 = 240.
        assert pay["fixed_per_day"] == 300.0
        assert pay["supplement_per_day"] == 300.0

    def test_lump_payout_takes_the_variable_part_out_of_the_per_day_figure(self, test_db):
        """Paying the variable part once means a taken day must not also carry it,
        or the same money is paid twice."""
        user = _make_user(test_db, person_id=99, wage=30000)
        user.vacation_variable_payout = "lump"
        test_db.commit()

        pay = calculate_vacation_pay(
            user=user,
            entitled_days=25,
            earning_start=datetime.date(2025, 4, 1),
            earning_end=datetime.date(2026, 3, 31),
            db=test_db,
            vacation_rates={"fixed_pct": 0.008, "variable_pct": 0.005, "payout_pct": 0.046},
        )

        assert pay["supplement_per_day"] == pay["fixed_per_day"]
        # The full figure survives for the payout path, which owes both parts.
        assert pay["full_supplement_per_day"] == round(pay["fixed_per_day"] + pay["variable_per_day"], 2)

    def test_lump_is_a_share_of_the_earning_year_not_a_per_day_amount(self, test_db, monkeypatch):
        """Semesterlagen's percentage rule: the lump is a share of the whole
        earning year's variable pay, settled once. It must not be scaled by the
        entitlement, which is what separates it from variable_per_day x days."""
        user = _make_user(test_db, person_id=1, wage=30000)
        user.vacation_variable_payout = "lump"
        user.vacation_variable_lump_pct = 0.12
        test_db.commit()

        # Two months of the earning year, 10 000 kr variable pay each.
        monkeypatch.setattr(
            "app.core.schedule.summary.summarize_month_for_person",
            lambda **kwargs: {"ob_pay": {"OB1": 6000.0}, "ot_pay": 3000.0, "oncall_pay": 1000.0},
        )

        def _pay(entitled_days):
            return calculate_vacation_pay(
                user=user,
                entitled_days=entitled_days,
                earning_start=datetime.date(2026, 1, 1),
                earning_end=datetime.date(2026, 2, 28),
                db=test_db,
                vacation_rates={"fixed_pct": 0.008, "variable_pct": 0.005, "payout_pct": 0.046},
            )

        full = _pay(25)
        assert full["variable_total"] == 20000.0
        assert full["variable_lump_total"] == 2400.0  # 20000 * 0.12

        # Half the entitlement, same lump: the rule is not per day.
        assert _pay(13)["variable_lump_total"] == 2400.0

    def test_supplement_uses_the_rates_in_force_during_the_vacation_year(self, test_db):
        """Rates are versioned through RateHistory, so a year running on rates opened at
        a transition must not be priced with whatever is current on the user row."""
        from app.core.rates import add_new_rates

        user = _make_user(test_db, person_id=1, wage=30000)
        user.custom_rates = {"vacation": {"fixed_pct": 0.004, "variable_pct": 0.0, "payout_pct": 0.046}}
        test_db.commit()
        # Empty rates from 2026-12-01 mean "back to the agreement's defaults".
        add_new_rates(test_db, user.id, {}, datetime.date(2026, 12, 1))

        pay = calculate_vacation_pay(
            user=user,
            entitled_days=25,
            # Earning year 2027/28 feeds vacation year 2028, well past the rate change.
            earning_start=datetime.date(2027, 4, 1),
            earning_end=datetime.date(2028, 3, 31),
            db=test_db,
        )

        # 0.8% of 30000, not the 0.4% still sitting on the user row.
        assert pay["fixed_pct"] == 0.008
        assert pay["fixed_per_day"] == 240.0

    def test_supplement_uses_the_salary_in_force_during_the_vacation_year(self, test_db):
        """Semesterlagen 16 a bases the supplement on the salary current when the
        vacation is taken, so a future year uses the raise already on record rather
        than today's salary."""
        from app.core.schedule.wages import add_new_wage

        user = _make_user(test_db, person_id=1, wage=30000)
        add_new_wage(test_db, user.id, 30000, datetime.date(2020, 1, 1))
        add_new_wage(test_db, user.id, 45000, datetime.date(2026, 12, 1))

        pay = calculate_vacation_pay(
            user=user,
            entitled_days=25,
            # Earning year 2026/27 feeds vacation year 2027, which runs on 45000.
            earning_start=datetime.date(2026, 4, 1),
            earning_end=datetime.date(2027, 3, 31),
            db=test_db,
            vacation_rates={"fixed_pct": 0.008, "variable_pct": 0.005, "payout_pct": 0.046},
        )

        assert pay["monthly_salary"] == 45000
        assert pay["fixed_per_day"] == 360.0  # 45000 * 0.008, not 30000 * 0.008

    def test_variable_pay_before_a_transition_belongs_to_the_consultant_employer(self, test_db, monkeypatch):
        """A transition inside the earning year splits it. The months worked before it
        are settled in the consultant employer's final payout, so counting them in the
        direct employment's supplement would pay for them twice."""
        from app.database.database import ConsultantSalaryType, EmploymentTransition

        user = _make_user(test_db, person_id=1, wage=30000)
        test_db.add(
            EmploymentTransition(
                user_id=user.id,
                transition_date=datetime.date(2026, 12, 1),
                consultant_salary_type=ConsultantSalaryType.TRAILING,
                consultant_vacation_days=0.0,
                consultant_supplement_pct=0.0043,
            )
        )
        test_db.commit()
        test_db.refresh(user)

        # 10 000 kr of variable pay every month of the earning year.
        monkeypatch.setattr(
            "app.core.schedule.summary.summarize_month_for_person",
            lambda **kw: {"ob_pay": {"OB1": 10000.0}, "ot_pay": 0.0, "oncall_pay": 0.0},
        )

        pay = calculate_vacation_pay(
            user=user,
            entitled_days=9,
            earning_start=datetime.date(2026, 4, 1),
            earning_end=datetime.date(2027, 3, 31),
            db=test_db,
            vacation_rates={"fixed_pct": 0.008, "variable_pct": 0.005, "payout_pct": 0.046},
        )

        # Worked Dec 2026 - Feb 2027 (paid Jan - Mar 2027): three months, not twelve.
        assert pay["variable_total"] == 30000.0
        assert pay["breakdown_start"] == datetime.date(2026, 12, 1)

    def test_variable_pay_counts_what_fell_due_inside_the_earning_year(self, test_db, monkeypatch):
        """Variable pay worked in March is paid in April, so with an earning year
        ending 31 March that March belongs to the next year, not the closing one.

        Both the lump and the per-day 0.5% part read that shifted window. The per-day
        figure used to run on the unshifted one, which made the same year worth a
        different amount depending only on how the employer settled it.
        """
        user = _make_user(test_db, person_id=1, wage=30000)
        user.vacation_variable_payout = "lump"
        user.vacation_variable_lump_pct = 0.12
        user.vacation_variable_lump_lag_months = 1
        test_db.commit()

        # Only Jan-Mar 2026 have variable pay, 10 000 kr each.
        earned = {(2026, 1): 10000.0, (2026, 2): 10000.0, (2026, 3): 10000.0}
        monkeypatch.setattr(
            "app.core.schedule.summary.summarize_month_for_person",
            lambda **kw: {
                "ob_pay": {"OB1": earned.get((kw["year"], kw["month"]), 0.0)},
                "ot_pay": 0.0,
                "oncall_pay": 0.0,
            },
        )

        pay = calculate_vacation_pay(
            user=user,
            entitled_days=25,
            earning_start=datetime.date(2025, 4, 1),
            earning_end=datetime.date(2026, 3, 31),
            db=test_db,
            vacation_rates={"fixed_pct": 0.008, "variable_pct": 0.005, "payout_pct": 0.046},
        )

        # Paid Apr 2025 - Mar 2026, i.e. worked Mar 2025 - Feb 2026: March drops out
        # of both figures, which now read the same window.
        assert pay["variable_total"] == 20000.0
        assert pay["variable_lump_base"] == 20000.0
        assert pay["variable_per_day"] == 100.0  # 20000 * 0.005, not 30000 * 0.005
        assert pay["variable_lump_total"] == 2400.0  # 20000 * 0.12
        assert pay["variable_lump_end"] == datetime.date(2026, 2, 1)

        # The breakdown the card renders follows the window the payout used, or
        # it would sum to a different total than the figure printed above it.
        assert pay["breakdown_total"] == pay["variable_lump_base"]
        assert pay["breakdown_ob_total"] == 20000.0
        assert (pay["breakdown_start"], pay["breakdown_end"]) == (pay["variable_lump_start"], pay["variable_lump_end"])

    def test_zero_lag_counts_the_earning_year_itself(self, test_db, monkeypatch):
        """An employer paying variable pay in the month it is worked has no shift."""
        user = _make_user(test_db, person_id=1, wage=30000)
        user.vacation_variable_payout = "lump"
        user.vacation_variable_lump_lag_months = 0
        test_db.commit()

        earned = {(2026, 1): 10000.0, (2026, 2): 10000.0, (2026, 3): 10000.0}
        monkeypatch.setattr(
            "app.core.schedule.summary.summarize_month_for_person",
            lambda **kw: {
                "ob_pay": {"OB1": earned.get((kw["year"], kw["month"]), 0.0)},
                "ot_pay": 0.0,
                "oncall_pay": 0.0,
            },
        )

        pay = calculate_vacation_pay(
            user=user,
            entitled_days=25,
            earning_start=datetime.date(2025, 4, 1),
            earning_end=datetime.date(2026, 3, 31),
            db=test_db,
            vacation_rates={"fixed_pct": 0.008, "variable_pct": 0.005, "payout_pct": 0.046},
        )

        assert pay["variable_lump_base"] == pay["variable_total"] == 30000.0

    def test_per_day_payout_pays_no_lump(self, test_db, monkeypatch):
        user = _make_user(test_db, person_id=1, wage=30000)
        monkeypatch.setattr(
            "app.core.schedule.summary.summarize_month_for_person",
            lambda **kwargs: {"ob_pay": {"OB1": 6000.0}, "ot_pay": 3000.0, "oncall_pay": 1000.0},
        )

        pay = calculate_vacation_pay(
            user=user,
            entitled_days=25,
            earning_start=datetime.date(2026, 1, 1),
            earning_end=datetime.date(2026, 2, 28),
            db=test_db,
            vacation_rates={"fixed_pct": 0.008, "variable_pct": 0.005, "payout_pct": 0.046},
        )

        assert pay["variable_lump_total"] == 0.0
        # The per-day rule still applies: 0.5% of the variable total, per day.
        assert pay["variable_per_day"] == round(20000.0 * 0.005, 2)
        # Without a lump the breakdown shows the unshifted earning year.
        assert pay["breakdown_total"] == pay["variable_total"]
        assert pay["breakdown_start"] == datetime.date(2026, 1, 1)

    def test_payout_settings_in_custom_rates_are_ignored(self, test_db):
        """These settings moved off custom_rates onto the User, because
        custom_rates is versioned through RateHistory and a wage revision would
        open a new rate period dragging the payout routine along with it. A
        leftover value in the rates dict must not quietly still work."""
        user = _make_user(test_db, person_id=99, wage=30000)
        pay = calculate_vacation_pay(
            user=user,
            entitled_days=25,
            earning_start=datetime.date(2025, 4, 1),
            earning_end=datetime.date(2026, 3, 31),
            db=test_db,
            vacation_rates={
                "fixed_pct": 0.008,
                "variable_pct": 0.005,
                "payout_pct": 0.046,
                "fixed_per_day": 999.0,
                "variable_payout": "lump",
            },
        )

        assert pay["fixed_per_day"] == 240.0
        assert pay["variable_payout"] == "per_day"


class TestVacationSupplementForMonth:
    """The month's supplement, split the way a payslip lists it."""

    def _pay(self, **kwargs):
        base = {
            "fixed_per_day": 240.0,
            "variable_per_day": 60.0,
            "supplement_per_day": 300.0,
            "full_supplement_per_day": 300.0,
            "variable_payout": "per_day",
            "variable_payout_month": None,
            "variable_lump_total": 0.0,
        }
        base.update(kwargs)
        return {"pay": base}

    def test_parts_sum_to_the_figure_the_views_showed_before_the_split(self):
        from app.core.schedule.vacation import vacation_supplement_for_month

        user = SimpleNamespace(vacation_year_start_month=4)
        result = vacation_supplement_for_month(self._pay(), user, month=7, sem_days=4)

        assert result["fixed"] == round(240.0 * 4, 0)
        assert result["fixed"] + result["variable"] == round(300.0 * 4, 0)
        assert result["lump"] == 0.0
        assert result["total"] == round(300.0 * 4, 0)

    def test_lump_lands_in_the_configured_month_only(self):
        from app.core.schedule.vacation import vacation_supplement_for_month

        user = SimpleNamespace(vacation_year_start_month=4)
        pay = self._pay(
            supplement_per_day=240.0,
            variable_payout="lump",
            variable_payout_month=6,
            variable_lump_total=1500.0,
        )

        june = vacation_supplement_for_month(pay, user, month=6, sem_days=0)
        july = vacation_supplement_for_month(pay, user, month=7, sem_days=4)

        assert june["lump"] == 1500.0
        assert june["total"] == 1500.0
        assert july["lump"] == 0.0
        # A day taken under lump payout carries the fixed part alone.
        assert july["variable"] == 0.0
        assert july["total"] == round(240.0 * 4, 0)

    def test_lump_falls_back_to_the_vacation_year_start_month(self):
        """Without a configured month the lump still needs a defined home."""
        from app.core.schedule.vacation import vacation_supplement_for_month

        user = SimpleNamespace(vacation_year_start_month=4)
        pay = self._pay(variable_payout="lump", variable_payout_month=None, variable_lump_total=1500.0)

        assert vacation_supplement_for_month(pay, user, month=4, sem_days=0)["lump"] == 1500.0
        assert vacation_supplement_for_month(pay, user, month=5, sem_days=0)["lump"] == 0.0
