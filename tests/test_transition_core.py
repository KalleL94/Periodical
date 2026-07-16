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
    calculate_consultant_vacation_days,
    calculate_consultant_vacation_payout,
    calculate_transition_month_summary,
    calculate_variable_avg_daily,
    get_earning_year,
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
):
    return SimpleNamespace(
        transition_date=transition_date,
        earning_year_start=earning_year_start,
        earning_year_end=earning_year_end,
        consultant_supplement_pct=consultant_supplement_pct,
        variable_avg_daily_override=variable_avg_daily_override,
        consultant_salary_type=consultant_salary_type,
    )


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
    def test_payout_math_with_auto_calculated_variable_pay(self, test_db, monkeypatch):
        user = _make_user(test_db, wage=30000)
        transition = _make_transition(transition_date=datetime.date(2026, 5, 1))

        monkeypatch.setattr(
            "app.core.schedule.transition.calculate_consultant_vacation_days",
            lambda *a, **k: 13,
        )
        monkeypatch.setattr(
            "app.core.schedule.transition.calculate_variable_avg_daily",
            lambda *a, **k: 40.0,
        )

        result = calculate_consultant_vacation_payout(transition, user, test_db)

        assert result["vacation_days"] == 13
        assert result["monthly_salary"] == 30000
        assert result["base_per_day"] == 1379.3103
        assert result["base_with_supplement_per_day"] == 1385.2414
        assert result["base_payout"] == 18008.14
        assert result["variable_avg_daily"] == 40.0
        assert result["variable_auto_calculated"] is True
        assert result["variable_payout"] == 520.0
        assert result["total"] == 18528.14

    def test_manual_variable_override_skips_auto_calculation(self, test_db, monkeypatch):
        user = _make_user(test_db, wage=30000)
        transition = _make_transition(
            transition_date=datetime.date(2026, 5, 1),
            variable_avg_daily_override=15.5,
        )

        monkeypatch.setattr(
            "app.core.schedule.transition.calculate_consultant_vacation_days",
            lambda *a, **k: 13,
        )

        def _should_not_be_called(*a, **k):
            raise AssertionError("calculate_variable_avg_daily must not run when override is set")

        monkeypatch.setattr(
            "app.core.schedule.transition.calculate_variable_avg_daily",
            _should_not_be_called,
        )

        result = calculate_consultant_vacation_payout(transition, user, test_db)

        assert result["variable_auto_calculated"] is False
        assert result["variable_avg_daily"] == 15.5
        assert result["variable_payout"] == 201.5
        assert result["base_payout"] == 18008.14
        assert result["total"] == 18209.64

    def test_zero_vacation_days_left_yields_zero_payout(self, test_db, monkeypatch):
        # Edge case: nothing left to pay out (e.g. all accrued days already used).
        user = _make_user(test_db, wage=30000)
        transition = _make_transition(transition_date=datetime.date(2026, 5, 1))

        monkeypatch.setattr(
            "app.core.schedule.transition.calculate_consultant_vacation_days",
            lambda *a, **k: 0,
        )
        monkeypatch.setattr(
            "app.core.schedule.transition.calculate_variable_avg_daily",
            lambda *a, **k: 40.0,
        )

        result = calculate_consultant_vacation_payout(transition, user, test_db)

        assert result["vacation_days"] == 0
        assert result["base_payout"] == 0.0
        assert result["variable_payout"] == 0.0
        assert result["total"] == 0.0


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

        monkeypatch.setattr(
            "app.core.schedule.transition.calculate_consultant_vacation_days",
            lambda *a, **k: 10,
        )
        monkeypatch.setattr(
            "app.core.schedule.transition.calculate_variable_avg_daily",
            lambda *a, **k: 0.0,
        )

        result = calculate_consultant_vacation_payout(transition, user, test_db)

        # Correct behavior: the consultant's payout must be based on the consultant's
        # actual final wage (28000), not the direct employer's new wage (32000).
        assert result["monthly_salary"] == 28000
