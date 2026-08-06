"""Unit tests for app.core.schedule.transition.

Covers the money-affecting core logic of the consultant -> direct employment
transition: earning-year resolution, net vacation-day accrual, the average
variable daily rate, the vacation payout (same-pay rule), and the transition
month's split-salary summary.

Money-math tests use hand-verified expected values (computed independently
of the implementation) rather than re-deriving the formula in the test, so
they actually pin the arithmetic rather than just mirror it.
"""

import datetime
from types import SimpleNamespace

from app.core.schedule.transition import (
    PERCENTAGE_RULE,
    SAME_PAY_RULE,
    _lump_already_paid,
    _shift_months,
    calculate_consultant_vacation_days,
    calculate_consultant_vacation_payout,
    calculate_transition_month_summary,
    calculate_variable_avg_daily,
    get_earning_year,
    get_earning_years,
)
from app.database.database import ConsultantSalaryType, User, UserRole


def _make_user(test_db, uid=1, wage=30000, employment_start_date=None, person_id=None):
    user = User(
        id=uid,
        username=f"user{uid}",
        password_hash="x",
        name=f"User {uid}",
        role=UserRole.USER,
        wage=wage,
        vacation={},
        must_change_password=0,
        employment_start_date=employment_start_date,
        person_id=person_id,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


def _make_transition(
    transition_date,
    earning_year_start=None,
    earning_year_end=None,
    consultant_supplement_pct=0.0043,
    variable_avg_daily_override=None,
    consultant_salary_type=ConsultantSalaryType.TRAILING,
    consultant_vacation_days=0.0,
):
    return SimpleNamespace(
        transition_date=transition_date,
        earning_year_start=earning_year_start,
        earning_year_end=earning_year_end,
        consultant_supplement_pct=consultant_supplement_pct,
        variable_avg_daily_override=variable_avg_daily_override,
        consultant_salary_type=consultant_salary_type,
        consultant_vacation_days=consultant_vacation_days,
    )


def _stub_years(monkeypatch, *rows, lump_settled=False, rule=SAME_PAY_RULE):
    """Pin get_earning_years so payout tests exercise the money math, not the accrual.

    Each row is (days, earned, variable, total_pay); dates are filler, the payout
    reads only the numbers. `lump_settled` and `rule` may each be a single value for
    every year or one per row, since both are resolved per earning year.
    """

    def _per_row(value):
        return list(value) if isinstance(value, (list, tuple)) else [value] * len(rows)

    flags, rules = _per_row(lump_settled), _per_row(rule)
    years = [
        {
            "start": datetime.date(2025, 4, 1),
            "end": datetime.date(2026, 3, 31),
            "vacation_year_start": datetime.date(2026, 4, 1),
            "earned": earned,
            "used": earned - days,
            "days": days,
            "variable": variable,
            "total_pay": total_pay,
            "lump_settled": settled,
            "rule": year_rule,
        }
        for (days, earned, variable, total_pay), settled, year_rule in zip(rows, flags, rules, strict=True)
    ]
    monkeypatch.setattr("app.core.schedule.transition.get_earning_years", lambda *a, **k: years)
    return years


class TestGetEarningYear:
    def test_manual_override_returned_as_is(self):
        transition = _make_transition(
            transition_date=datetime.date(2026, 6, 1),
            earning_year_start=datetime.date(2024, 1, 1),
            earning_year_end=datetime.date(2024, 12, 31),
        )
        start, end = get_earning_year(transition)
        assert start == datetime.date(2024, 1, 1)
        assert end == datetime.date(2024, 12, 31)

    def test_auto_mode_transition_mid_year(self):
        # transition_date = 2026-06-15 -> last consultant day 2026-06-14 (month >= 4)
        transition = _make_transition(transition_date=datetime.date(2026, 6, 15))
        start, end = get_earning_year(transition)
        assert start == datetime.date(2026, 4, 1)
        assert end == datetime.date(2026, 6, 14)

    def test_auto_mode_transition_before_april(self):
        # transition_date = 2026-02-01 -> last consultant day 2026-01-31 (month < 4)
        # falls in the earning year that started the previous April.
        transition = _make_transition(transition_date=datetime.date(2026, 2, 1))
        start, end = get_earning_year(transition)
        assert start == datetime.date(2025, 4, 1)
        assert end == datetime.date(2026, 1, 31)

    def test_earning_year_boundary_respected_on_april_first(self):
        # transition_date = 2026-04-01 -> last consultant day 2026-03-31, which
        # belongs to the *previous* earning year (April is the cutover month).
        transition = _make_transition(transition_date=datetime.date(2026, 4, 1))
        start, end = get_earning_year(transition)
        assert start == datetime.date(2025, 4, 1)
        assert end == datetime.date(2026, 3, 31)

    def test_earning_year_boundary_respected_day_after_april_first(self):
        # transition_date = 2026-04-02 -> last consultant day 2026-04-01, which
        # now belongs to the *new* earning year that just started.
        transition = _make_transition(transition_date=datetime.date(2026, 4, 2))
        start, end = get_earning_year(transition)
        assert start == datetime.date(2026, 4, 1)
        assert end == datetime.date(2026, 4, 1)


class TestCalculateConsultantVacationDays:
    def test_returns_none_without_employment_start_date(self, test_db):
        user = _make_user(test_db, employment_start_date=None)
        transition = _make_transition(transition_date=datetime.date(2026, 6, 1))
        assert calculate_consultant_vacation_days(user, transition) is None

    def test_manual_earning_year_prorates_by_employed_days(self, test_db):
        # employed 2025-10-01..2026-03-31 (182 of 365 days) -> ceil(25 * 182/365) = 13
        user = _make_user(test_db, employment_start_date=datetime.date(2025, 10, 1))
        transition = _make_transition(
            transition_date=datetime.date(2026, 4, 15),
            earning_year_start=datetime.date(2025, 4, 1),
            earning_year_end=datetime.date(2026, 3, 31),
        )
        assert calculate_consultant_vacation_days(user, transition) == 13

    def test_manual_earning_year_no_overlap_returns_zero(self, test_db):
        # Employment starts after the manual earning year ends -> no overlap.
        user = _make_user(test_db, employment_start_date=datetime.date(2026, 5, 1))
        transition = _make_transition(
            transition_date=datetime.date(2026, 6, 1),
            earning_year_start=datetime.date(2025, 4, 1),
            earning_year_end=datetime.date(2026, 3, 31),
        )
        assert calculate_consultant_vacation_days(user, transition) == 0

    def test_auto_mode_full_earning_year_earns_full_entitlement(self, test_db):
        # Employed the entire April-March earning year -> full 25 days, no session.
        user = _make_user(test_db, employment_start_date=datetime.date(2025, 4, 1))
        transition = _make_transition(transition_date=datetime.date(2026, 4, 1))
        assert calculate_consultant_vacation_days(user, transition) == 25

    def test_auto_mode_partial_earning_year_prorates(self, test_db):
        # Employed 2025-04-01..2025-09-30 (183 of 365 days) -> ceil(25 * 183/365) = 13
        user = _make_user(test_db, employment_start_date=datetime.date(2025, 4, 1))
        transition = _make_transition(transition_date=datetime.date(2025, 10, 1))
        assert calculate_consultant_vacation_days(user, transition) == 13

    def test_auto_mode_deducts_already_used_days_when_session_given(self, test_db, monkeypatch):
        # The deduction lookup for earning year N only triggers once the vacation year
        # that follows it (starting the same April that closes year N) has itself begun,
        # which unavoidably pulls a second, adjacent earning-year iteration into the loop
        # (1 day of it, since transition_date is one day into that following April):
        #   year 1 (2025-04-01..2026-03-31): earns 25, 5 already used -> nets 20
        #   year 2 (2026-04-01..2026-04-01, 1 day employed): earns ceil(25*1/365) = 1
        # Total = 21.
        user = _make_user(test_db, employment_start_date=datetime.date(2025, 4, 1))
        transition = _make_transition(transition_date=datetime.date(2026, 4, 2))

        monkeypatch.setattr(
            "app.core.schedule.vacation.count_vacation_days_used",
            lambda **kwargs: {"total": 5},
        )

        assert calculate_consultant_vacation_days(user, transition, session=test_db) == 21

    def test_auto_mode_used_days_cannot_go_negative(self, test_db, monkeypatch):
        # Edge case: more days used (30) than earned (25) in year 1 must clamp that
        # year's contribution at 0, never subtract into a negative payout. Year 2 still
        # contributes its own genuine 1-day sliver (see test above), so the total is 1,
        # not -5 -- proof the clamp applies per-year rather than to the running total.
        user = _make_user(test_db, employment_start_date=datetime.date(2025, 4, 1))
        transition = _make_transition(transition_date=datetime.date(2026, 4, 2))

        monkeypatch.setattr(
            "app.core.schedule.vacation.count_vacation_days_used",
            lambda **kwargs: {"total": 30},
        )

        assert calculate_consultant_vacation_days(user, transition, session=test_db) == 1


class TestCalculateVariableAvgDaily:
    def test_returns_none_for_out_of_range_person_id(self, test_db):
        user = _make_user(test_db, uid=20, person_id=99)
        result = calculate_variable_avg_daily(user, test_db, datetime.date(2026, 1, 1), datetime.date(2026, 2, 28))
        assert result is None

    def test_returns_none_when_period_data_raises(self, test_db, monkeypatch):
        user = _make_user(test_db)
        monkeypatch.setattr(
            "app.core.schedule.period.generate_period_data",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        result = calculate_variable_avg_daily(user, test_db, datetime.date(2026, 1, 1), datetime.date(2026, 2, 28))
        assert result is None

    def test_returns_none_when_no_working_days(self, test_db, monkeypatch):
        user = _make_user(test_db)
        monkeypatch.setattr(
            "app.core.schedule.period.generate_period_data",
            lambda **kwargs: [{"shift": SimpleNamespace(code="OFF")} for _ in range(5)],
        )
        result = calculate_variable_avg_daily(user, test_db, datetime.date(2026, 1, 1), datetime.date(2026, 2, 28))
        assert result is None

    def test_returns_none_when_total_variable_pay_is_zero(self, test_db, monkeypatch):
        user = _make_user(test_db)
        monkeypatch.setattr(
            "app.core.schedule.period.generate_period_data",
            lambda **kwargs: [{"shift": SimpleNamespace(code="N1")} for _ in range(5)],
        )
        monkeypatch.setattr(
            "app.core.schedule.summary.summarize_month_for_person",
            lambda **kwargs: {"ob_pay": {}, "ot_pay": 0.0, "oncall_pay": 0.0},
        )
        result = calculate_variable_avg_daily(user, test_db, datetime.date(2026, 1, 1), datetime.date(2026, 2, 28))
        assert result is None

    def test_computes_average_variable_pay_per_working_day(self, test_db, monkeypatch):
        user = _make_user(test_db)

        monkeypatch.setattr(
            "app.core.schedule.period.generate_period_data",
            lambda **kwargs: [{"shift": SimpleNamespace(code="N1")} for _ in range(10)],
        )

        def fake_summary(**kwargs):
            if (kwargs["year"], kwargs["month"]) == (2026, 1):
                return {"ob_pay": {"OB1": 100.0}, "ot_pay": 50.0, "oncall_pay": 25.0}  # 175
            return {"ob_pay": {"OB1": 200.0}, "ot_pay": 0.0, "oncall_pay": 25.0}  # 225

        monkeypatch.setattr("app.core.schedule.summary.summarize_month_for_person", fake_summary)

        result = calculate_variable_avg_daily(user, test_db, datetime.date(2026, 1, 1), datetime.date(2026, 2, 28))
        # (175 + 225) / 10 working days = 40.0
        assert result == 40.0

    def test_before_employment_days_excluded_from_working_days(self, test_db, monkeypatch):
        user = _make_user(test_db)

        monkeypatch.setattr(
            "app.core.schedule.period.generate_period_data",
            lambda **kwargs: [
                {"shift": SimpleNamespace(code="N1"), "before_employment": True},
                {"shift": SimpleNamespace(code="N1")},
            ],
        )
        monkeypatch.setattr(
            "app.core.schedule.summary.summarize_month_for_person",
            lambda **kwargs: {"ob_pay": {"OB1": 100.0}, "ot_pay": 0.0, "oncall_pay": 0.0},
        )

        result = calculate_variable_avg_daily(user, test_db, datetime.date(2026, 1, 1), datetime.date(2026, 1, 31))
        # Only 1 real working day counted -> 100.0 / 1 = 100.0, not / 2.
        assert result == 100.0


class TestCalculateConsultantVacationPayout:
    """Same-pay rule (Semesterlagen 16 a).

    Per unused day: 4.6% of the monthly salary + the supplement, plus 0.5% of the
    variable pay earned in that day's own earning year.
    """

    def test_payout_math_with_auto_calculated_variable_pay(self, test_db, monkeypatch):
        user = _make_user(test_db, wage=30000)
        transition = _make_transition(transition_date=datetime.date(2026, 5, 1))
        # 8000 variable -> 0.5% = 40.00 per day.
        _stub_years(monkeypatch, (13, 13, 8000.0, 188000.0))

        result = calculate_consultant_vacation_payout(transition, user, test_db)

        assert result["rule"] == SAME_PAY_RULE
        assert result["vacation_days"] == 13
        assert result["monthly_salary"] == 30000
        # 30000 * 0.046 = 1380.00; + 0.43% supplement -> 30000 * 0.0503 = 1509.00
        assert result["base_per_day"] == 1380.0
        assert result["base_with_supplement_per_day"] == 1509.0
        assert result["base_payout"] == 19617.0
        assert result["variable_avg_daily"] == 40.0
        assert result["variable_auto_calculated"] is True
        assert result["variable_payout"] == 520.0
        assert result["total"] == 20137.0

    def test_each_earning_year_uses_its_own_variable_pay(self, test_db, monkeypatch):
        # The older year earned less variable pay, so its days are worth less. Paying
        # both years at the latest year's rate is the bug this pins.
        user = _make_user(test_db, wage=30000)
        transition = _make_transition(transition_date=datetime.date(2026, 5, 1))
        _stub_years(monkeypatch, (5, 13, 8000.0, 188000.0), (17, 17, 20000.0, 320000.0))

        result = calculate_consultant_vacation_payout(transition, user, test_db)

        assert result["vacation_days"] == 22
        # 0.5% of 8000 = 40.00/day on 5 days; 0.5% of 20000 = 100.00/day on 17 days
        assert result["variable_payout"] == 1900.0
        assert result["base_payout"] == 33198.0  # 1509.00 * 22
        assert result["total"] == 35098.0
        assert [year["per_day"] for year in result["years"]] == [1549.0, 1609.0]
        assert [year["payout"] for year in result["years"]] == [7745.0, 27353.0]

    def test_manual_variable_override_skips_auto_calculation(self, test_db, monkeypatch):
        user = _make_user(test_db, wage=30000)
        transition = _make_transition(
            transition_date=datetime.date(2026, 5, 1),
            variable_avg_daily_override=15.5,
        )
        # A variable pay that would have produced 40.00/day if it were consulted.
        _stub_years(monkeypatch, (13, 13, 8000.0, 188000.0))

        result = calculate_consultant_vacation_payout(transition, user, test_db)

        assert result["variable_auto_calculated"] is False
        assert result["variable_avg_daily"] == 15.5
        assert result["variable_payout"] == 201.5
        assert result["base_payout"] == 19617.0
        assert result["total"] == 19818.5

    def test_manual_day_override_replaces_the_calculated_days(self, test_db, monkeypatch):
        # The stored day count only overrides when it disagrees with the calculation;
        # it is filled oldest year first, because vacation days are consumed oldest first.
        user = _make_user(test_db, wage=30000)
        transition = _make_transition(
            transition_date=datetime.date(2026, 5, 1),
            consultant_vacation_days=20.0,
        )
        _stub_years(monkeypatch, (5, 13, 8000.0, 188000.0), (17, 17, 20000.0, 320000.0))

        result = calculate_consultant_vacation_payout(transition, user, test_db)

        assert result["vacation_days"] == 20
        assert [year["days"] for year in result["years"]] == [13, 7]

    def test_stored_days_matching_the_calculation_is_not_an_override(self, test_db, monkeypatch):
        # The form stores the auto total when the override field is blank, so an equal
        # value must not be mistaken for a deliberate override.
        user = _make_user(test_db, wage=30000)
        transition = _make_transition(
            transition_date=datetime.date(2026, 5, 1),
            consultant_vacation_days=22.0,
        )
        _stub_years(monkeypatch, (5, 13, 8000.0, 188000.0), (17, 17, 20000.0, 320000.0))

        result = calculate_consultant_vacation_payout(transition, user, test_db)

        assert result["vacation_days"] == 22
        assert [year["days"] for year in result["years"]] == [5, 17]

    def test_zero_vacation_days_left_yields_zero_payout(self, test_db, monkeypatch):
        # Edge case: nothing left to pay out (e.g. all accrued days already used).
        user = _make_user(test_db, wage=30000)
        transition = _make_transition(transition_date=datetime.date(2026, 5, 1))
        _stub_years(monkeypatch, (0, 13, 8000.0, 188000.0))

        result = calculate_consultant_vacation_payout(transition, user, test_db)

        assert result["vacation_days"] == 0
        assert result["base_payout"] == 0.0
        assert result["variable_payout"] == 0.0
        assert result["total"] == 0.0


class TestEarningYearVariableWindows:
    """The windows the variable pay is summed over must tile the whole engagement.

    Variable pay lags a month, so each year's window shifts back. The final year is the
    exception: the engagement ends there and the consultant employer settles the last
    months along with everything else, so its window runs to the last consultant day. A
    gap between the years would silently drop a month of variable pay from the payout.
    """

    def _windows(self, test_db, monkeypatch, user, transition):
        calls = []

        def _record(_user, _session, start, end):
            calls.append((start, end))
            return 0.0, 0.0

        monkeypatch.setattr("app.core.schedule.transition._pay_for_window", _record)
        get_earning_years(user, transition, session=test_db)
        # Each row asks for the shifted variable window first, then the unshifted base one.
        return calls[::2]

    def test_final_year_runs_to_the_last_consultant_day(self, test_db, monkeypatch):
        user = _make_user(test_db, employment_start_date=datetime.date(2025, 10, 1), person_id=1)
        transition = _make_transition(transition_date=datetime.date(2026, 12, 1))

        windows = self._windows(test_db, monkeypatch, user, transition)

        assert windows == [
            # earning year 2025/26, shifted a month back at both ends
            (datetime.date(2025, 9, 1), datetime.date(2026, 2, 28)),
            # final year: shifted start, but the end stays on the last consultant day
            (datetime.date(2026, 3, 1), datetime.date(2026, 11, 30)),
        ]

    def test_windows_leave_no_gap_between_the_years(self, test_db, monkeypatch):
        user = _make_user(test_db, employment_start_date=datetime.date(2024, 1, 1), person_id=1)
        transition = _make_transition(transition_date=datetime.date(2026, 12, 1))

        windows = self._windows(test_db, monkeypatch, user, transition)

        assert len(windows) == 4  # earning years 2023/24 through 2026/27
        for (_, earlier_end), (later_start, _) in zip(windows, windows[1:], strict=False):
            assert later_start == earlier_end + datetime.timedelta(days=1)


class TestVariableLumpAlreadySettled:
    """A year whose variable supplement was paid as a lump must not pay it again.

    The employer settles the whole earning year in one month of the following vacation
    year. If that month is behind the transition, the money is out the door, and paying
    0.5% per unused day on top would hand over the same variable pay twice.
    """

    def test_settled_year_pays_only_the_base_component(self, test_db, monkeypatch):
        user = _make_user(test_db, wage=30000)
        transition = _make_transition(transition_date=datetime.date(2026, 5, 1))
        _stub_years(monkeypatch, (5, 13, 8000.0, 188000.0), lump_settled=True)

        result = calculate_consultant_vacation_payout(transition, user, test_db)

        assert result["variable_payout"] == 0.0
        assert result["years"][0]["per_day"] == 1509.0  # 30000 * 0.0503, no variable part
        assert result["total"] == 7545.0

    def test_only_the_settled_year_loses_its_variable_part(self, test_db, monkeypatch):
        user = _make_user(test_db, wage=30000)
        transition = _make_transition(transition_date=datetime.date(2026, 5, 1))
        _stub_years(
            monkeypatch,
            (5, 13, 8000.0, 188000.0),
            (17, 17, 20000.0, 320000.0),
            lump_settled=(True, False),
        )

        result = calculate_consultant_vacation_payout(transition, user, test_db)

        # Only the second year keeps its 0.5%: 100.00/day on 17 days.
        assert result["variable_payout"] == 1700.0
        assert [year["per_day"] for year in result["years"]] == [1509.0, 1609.0]

    def test_explicit_override_still_wins_over_a_settled_lump(self, test_db, monkeypatch):
        user = _make_user(test_db, wage=30000)
        transition = _make_transition(
            transition_date=datetime.date(2026, 5, 1),
            variable_avg_daily_override=15.5,
        )
        _stub_years(monkeypatch, (5, 13, 8000.0, 188000.0), lump_settled=True)

        result = calculate_consultant_vacation_payout(transition, user, test_db)

        assert result["variable_payout"] == 77.5


class TestLumpAlreadyPaid:
    def test_lump_month_before_the_transition_counts_as_paid(self, test_db):
        user = _make_user(test_db)
        user.vacation_variable_payout = "lump"
        user.vacation_variable_payout_month = 7
        # Vacation year starts 2026-04-01 -> the lump lands 2026-07-01, before the transition.
        assert _lump_already_paid(user, datetime.date(2026, 4, 1), datetime.date(2026, 12, 1)) is True

    def test_lump_month_after_the_transition_is_not_yet_paid(self, test_db):
        user = _make_user(test_db)
        user.vacation_variable_payout = "lump"
        user.vacation_variable_payout_month = 7
        # Vacation year starts 2027-04-01 -> the lump lands 2027-07-01, after the transition.
        assert _lump_already_paid(user, datetime.date(2027, 4, 1), datetime.date(2026, 12, 1)) is False

    def test_lump_month_before_the_vacation_year_start_rolls_into_the_next_year(self, test_db):
        user = _make_user(test_db)
        user.vacation_variable_payout = "lump"
        user.vacation_variable_payout_month = 2
        # Vacation year starts in April, so February belongs to the calendar year after.
        assert _lump_already_paid(user, datetime.date(2026, 4, 1), datetime.date(2027, 1, 1)) is False
        assert _lump_already_paid(user, datetime.date(2026, 4, 1), datetime.date(2027, 3, 1)) is True

    def test_per_day_payout_is_never_treated_as_settled(self, test_db):
        user = _make_user(test_db)
        user.vacation_variable_payout = "per_day"
        user.vacation_variable_payout_month = 7
        assert _lump_already_paid(user, datetime.date(2026, 4, 1), datetime.date(2026, 12, 1)) is False


class TestShiftMonths:
    def test_shifts_back_and_keeps_the_day(self):
        assert _shift_months(datetime.date(2026, 4, 1), 1) == datetime.date(2026, 3, 1)

    def test_shifts_back_to_the_month_end(self):
        assert _shift_months(datetime.date(2026, 11, 30), 1, to_month_end=True) == datetime.date(2026, 10, 31)

    def test_crosses_the_year_boundary(self):
        assert _shift_months(datetime.date(2026, 1, 15), 1) == datetime.date(2025, 12, 15)

    def test_clamps_a_day_the_target_month_does_not_have(self):
        assert _shift_months(datetime.date(2026, 3, 31), 1) == datetime.date(2026, 2, 28)


class TestPercentageRulePayout:
    """Percentage rule (Semesterlagen 16 b).

    12% of the earning year's pay, spread over the days that year earned. It is the
    whole vacation pay, so the monthly salary and the supplement play no part.
    """

    def test_payout_is_twelve_percent_spread_over_the_days_the_year_earned(self, test_db, monkeypatch):
        user = _make_user(test_db, wage=30000)
        transition = _make_transition(transition_date=datetime.date(2026, 5, 1))
        _stub_years(monkeypatch, (13, 13, 8000.0, 188000.0), rule=PERCENTAGE_RULE)

        result = calculate_consultant_vacation_payout(transition, user, test_db)

        assert result["rule"] == PERCENTAGE_RULE
        # 0.12 * 188000 = 22560.00 over 13 earned days = 1735.38/day, all 13 unused
        assert result["years"][0]["per_day"] == 1735.38
        assert result["base_payout"] == 22560.0
        assert result["variable_payout"] == 0.0
        assert result["total"] == 22560.0

    def test_days_already_taken_keep_their_share_of_the_underlying_pay(self, test_db, monkeypatch):
        # Dividing by the days earned, not the days left, is what stops the unused days
        # from absorbing the share the taken days already drew.
        user = _make_user(test_db, wage=30000)
        transition = _make_transition(transition_date=datetime.date(2026, 5, 1))
        _stub_years(monkeypatch, (5, 13, 8000.0, 188000.0), rule=PERCENTAGE_RULE)

        result = calculate_consultant_vacation_payout(transition, user, test_db)

        assert result["vacation_days"] == 5
        assert result["years"][0]["per_day"] == 1735.38
        assert result["total"] == 8676.92

    def test_supplement_and_variable_override_do_not_affect_the_percentage_rule(self, test_db, monkeypatch):
        user = _make_user(test_db, wage=30000)
        transition = _make_transition(
            transition_date=datetime.date(2026, 5, 1),
            consultant_supplement_pct=0.05,
            variable_avg_daily_override=999.0,
        )
        _stub_years(monkeypatch, (13, 13, 8000.0, 188000.0), rule=PERCENTAGE_RULE)

        result = calculate_consultant_vacation_payout(transition, user, test_db)

        assert result["base_per_day"] == 0.0
        assert result["variable_payout"] == 0.0
        assert result["total"] == 22560.0


class TestCalculateTransitionMonthSummary:
    def test_trailing_salary_type_splits_month_on_first_of_month_transition(self, test_db, monkeypatch):
        # Transition on the 1st of the month -> last consultant day is the last day of
        # the *previous* month; this is the common case and an explicit month-boundary edge.
        # Dates are chosen in the future (relative to the fixed test "today") so the wage
        # snapshot on the User row is not yet bumped by add_new_wage -- see the dedicated
        # xfail test below for the boundary bug that appears when a transition is recorded
        # on or after its own effective date.
        from app.core.schedule.wages import add_new_wage

        user = _make_user(test_db, wage=30000, employment_start_date=datetime.date(2020, 1, 1))
        add_new_wage(test_db, user.id, 28000, datetime.date(2020, 1, 1))
        add_new_wage(test_db, user.id, 32000, datetime.date(2027, 6, 1))

        transition = _make_transition(
            transition_date=datetime.date(2027, 6, 1),
            consultant_salary_type=ConsultantSalaryType.TRAILING,
        )

        monkeypatch.setattr(
            "app.core.schedule.transition.calculate_consultant_vacation_payout",
            lambda *a, **k: {"total": 5000.0, "vacation_days": 13},
        )
        monkeypatch.setattr(
            "app.core.schedule.summary.summarize_month_for_person",
            lambda **kwargs: {"ob_pay": {"OB1": 300.0}, "ot_pay": 100.0, "oncall_pay": 50.0},
        )

        result = calculate_transition_month_summary(transition, user, test_db)

        assert result["transition_year"] == 2027
        assert result["transition_month"] == 6
        assert result["consultant_salary_type"] == "trailing"
        assert result["consultant_employer"]["trailing_base"] == 28000.0
        assert result["consultant_employer"]["trailing_variable"] == 450.0
        assert result["consultant_employer"]["trailing_variable_breakdown"] == {
            "ob": 300.0,
            "oncall": 50.0,
            "ot": 100.0,
        }
        assert result["consultant_employer"]["vacation_payout"] == {"total": 5000.0, "vacation_days": 13}
        assert result["consultant_employer"]["total"] == 33450.0
        assert result["direct_employer"]["base_salary"] == 32000
        assert result["grand_total_gross"] == 65450.0

    def test_current_salary_type_excludes_trailing_base_on_last_of_month_transition(self, test_db, monkeypatch):
        # Transition on the last day of a month is still a valid, if unusual, boundary;
        # CURRENT type never pays trailing base/variable regardless of the day chosen.
        from app.core.schedule.wages import add_new_wage

        user = _make_user(test_db, wage=30000, employment_start_date=datetime.date(2020, 1, 1))
        add_new_wage(test_db, user.id, 28000, datetime.date(2020, 1, 1))
        add_new_wage(test_db, user.id, 32000, datetime.date(2027, 6, 30))

        transition = _make_transition(
            transition_date=datetime.date(2027, 6, 30),
            consultant_salary_type=ConsultantSalaryType.CURRENT,
        )

        monkeypatch.setattr(
            "app.core.schedule.transition.calculate_consultant_vacation_payout",
            lambda *a, **k: {"total": 2000.0, "vacation_days": 5},
        )

        result = calculate_transition_month_summary(transition, user, test_db)

        assert result["consultant_salary_type"] == "current"
        assert result["consultant_employer"]["trailing_base"] is None
        assert result["consultant_employer"]["trailing_variable"] is None
        assert result["consultant_employer"]["trailing_variable_breakdown"] is None
        assert result["consultant_employer"]["total"] == 2000.0
        assert result["direct_employer"]["base_salary"] == 32000
        assert result["grand_total_gross"] == 34000.0


class TestKnownBugWageBoundaryOnBackdatedTransition:
    """Regression test for a wage-resolution boundary bug surfaced through transition.py.

    Root cause (fixed): app.core.schedule.wages.get_user_wage() queried WageHistory
    with `effective_to > effective_date` (a strictly-greater comparison), while
    add_new_wage() closes the previous record with
    `effective_to = new_effective_from - 1 day` (meant as the *last inclusive day*
    of the old wage). Those two conventions disagreed on that exact boundary day:
    the closed record no longer matched (effective_to was not > that day), and the
    new record didn't match either (its effective_from is the day after). The
    function then fell through to the `User.wage` snapshot -- which is only correct
    if that snapshot hasn't already been bumped to the new wage.

    add_new_wage() bumps the snapshot immediately whenever effective_from <= today.
    So the fallback used to silently return the *new* wage instead of the old one
    whenever a caller asked for the wage on the day before a raise that had already
    taken effect (transition_date <= today) -- exactly what
    calculate_consultant_vacation_payout() and calculate_transition_month_summary()
    do for `last_consultant_day = transition.transition_date - 1 day`.

    This self-corrected for transitions scheduled in the future (the common case --
    see the tests above, which use future dates), but it silently paid the
    consultant's vacation payout and trailing base salary using the *direct
    employer's new wage* instead of the consultant's actual final wage whenever the
    transition was recorded on or after its own effective date (e.g. entered
    on/after the employee's first direct day, or backfilled after the fact).

    Fixed in app/core/schedule/wages.py by comparing `effective_to >= effective_date`
    so the closed record's inclusive last day is matched correctly.
    """

    def test_vacation_payout_uses_consultant_wage_not_new_direct_wage(self, test_db, monkeypatch):
        from app.core.schedule.wages import add_new_wage

        user = _make_user(test_db, wage=28000, employment_start_date=datetime.date(2020, 1, 1))
        add_new_wage(test_db, user.id, 28000, datetime.date(2020, 1, 1))
        # transition_date is NOT in the future -> this reproduces a transition being
        # recorded on/after the day it takes effect (e.g. entered the same day, or
        # backfilled), which is a perfectly normal way to use this feature.
        transition_date = datetime.date(2026, 6, 1)
        add_new_wage(test_db, user.id, 32000, transition_date)

        transition = _make_transition(transition_date=transition_date)

        _stub_years(monkeypatch, (10, 10, 0.0, 280000.0))

        result = calculate_consultant_vacation_payout(transition, user, test_db)

        # Correct behavior: the consultant's payout must be based on the consultant's
        # actual final wage (28000), not the direct employer's new wage (32000).
        assert result["monthly_salary"] == 28000
