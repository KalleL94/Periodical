"""Integration tests for the employment transition routes.

Exercises the full HTTP path for /profile/transition (GET/POST) and
/profile/transition/delete: form parsing, domain validation surfaced as
form errors, the PRG redirect, DB mutations, and authentication.

Each user manages only their own transition record (no id in the URL), so
"authorization" here means "must be authenticated" -- there is no
cross-user ownership check to exercise.
"""

import datetime

from app.core.rates import add_new_rates
from app.database.database import ConsultantSalaryType, EmploymentTransition, RateHistory, WageHistory


def _login(client, username, password):
    client.post("/login", data={"username": username, "password": password})


def _valid_form(**overrides):
    data = {
        "transition_date": "2027-06-01",
        "consultant_salary_type": "trailing",
        "consultant_vacation_days": "13",
        "consultant_supplement_pct": "0.0043",
        "variable_avg_daily_override": "",
        "earning_year_start": "",
        "earning_year_end": "",
        "notes": "",
        "new_direct_salary": "",
        "reset_rates_to_default": "",
    }
    data.update(overrides)
    return data


class TestTransitionPageAuth:
    def test_get_requires_authentication(self, test_client, test_db):
        resp = test_client.get("/profile/transition", follow_redirects=False)
        assert resp.status_code == 401

    def test_get_renders_for_authenticated_user(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")
        resp = test_client.get("/profile/transition")
        assert resp.status_code == 200


class TestTransitionSave:
    def test_creates_new_transition_record(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")

        resp = test_client.post(
            "/profile/transition",
            data=_valid_form(),
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert resp.headers["location"] == "/profile/transition"

        record = test_db.query(EmploymentTransition).filter(EmploymentTransition.user_id == test_user.id).first()
        assert record is not None
        assert record.transition_date == datetime.date(2027, 6, 1)
        assert record.consultant_salary_type == ConsultantSalaryType.TRAILING
        assert record.consultant_vacation_days == 13.0
        assert record.consultant_supplement_pct == 0.0043

    def test_updates_existing_record_instead_of_duplicating(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")
        test_client.post("/profile/transition", data=_valid_form(), follow_redirects=False)

        resp = test_client.post(
            "/profile/transition",
            data=_valid_form(consultant_salary_type="current", consultant_vacation_days="20"),
            follow_redirects=False,
        )
        assert resp.status_code == 302

        records = test_db.query(EmploymentTransition).filter(EmploymentTransition.user_id == test_user.id).all()
        assert len(records) == 1
        assert records[0].consultant_salary_type == ConsultantSalaryType.CURRENT
        assert records[0].consultant_vacation_days == 20.0

    def test_invalid_transition_date_returns_400(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")

        resp = test_client.post(
            "/profile/transition",
            data=_valid_form(transition_date="not-a-date"),
            follow_redirects=False,
        )
        assert resp.status_code == 400
        assert test_db.query(EmploymentTransition).filter(EmploymentTransition.user_id == test_user.id).first() is None

    def test_invalid_salary_type_returns_400(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")

        resp = test_client.post(
            "/profile/transition",
            data=_valid_form(consultant_salary_type="bogus"),
            follow_redirects=False,
        )
        assert resp.status_code == 400
        assert test_db.query(EmploymentTransition).filter(EmploymentTransition.user_id == test_user.id).first() is None

    def test_supplement_pct_at_or_above_one_returns_400(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")

        resp = test_client.post(
            "/profile/transition",
            data=_valid_form(consultant_supplement_pct="1.0"),
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_supplement_pct_at_or_below_zero_returns_400(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")

        resp = test_client.post(
            "/profile/transition",
            data=_valid_form(consultant_supplement_pct="0"),
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_invalid_variable_override_returns_400(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")

        resp = test_client.post(
            "/profile/transition",
            data=_valid_form(variable_avg_daily_override="not-a-number"),
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_invalid_earning_year_start_returns_400(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")

        resp = test_client.post(
            "/profile/transition",
            data=_valid_form(earning_year_start="not-a-date"),
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_invalid_earning_year_end_returns_400(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")

        resp = test_client.post(
            "/profile/transition",
            data=_valid_form(earning_year_end="not-a-date"),
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_invalid_vacation_days_returns_400(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")

        resp = test_client.post(
            "/profile/transition",
            data=_valid_form(consultant_vacation_days="abc"),
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_blank_vacation_days_auto_calculates_to_zero_without_employment_date(self, test_client, test_db, test_user):
        # test_user has no employment_start_date, so the auto-calculation has nothing
        # to work from and must fall back to 0, not error out.
        _login(test_client, "testuser", "testpass123")

        resp = test_client.post(
            "/profile/transition",
            data=_valid_form(consultant_vacation_days=""),
            follow_redirects=False,
        )
        assert resp.status_code == 302

        record = test_db.query(EmploymentTransition).filter(EmploymentTransition.user_id == test_user.id).first()
        assert record.consultant_vacation_days == 0.0

    def test_new_direct_salary_creates_wage_history_entry(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")

        resp = test_client.post(
            "/profile/transition",
            data=_valid_form(new_direct_salary="40000"),
            follow_redirects=False,
        )
        assert resp.status_code == 302

        wage = (
            test_db.query(WageHistory)
            .filter(
                WageHistory.user_id == test_user.id,
                WageHistory.effective_from == datetime.date(2027, 6, 1),
            )
            .first()
        )
        assert wage is not None
        assert wage.wage == 40000

    def test_reset_rates_to_default_clears_schedule_cache(self, test_client, test_db, test_user, monkeypatch):
        _login(test_client, "testuser", "testpass123")

        calls = []
        monkeypatch.setattr("app.core.schedule.clear_schedule_cache", lambda: calls.append(1))

        resp = test_client.post(
            "/profile/transition",
            data=_valid_form(reset_rates_to_default="on"),
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert calls

    def test_unauthenticated_post_returns_401(self, test_client, test_db):
        resp = test_client.post(
            "/profile/transition",
            data=_valid_form(),
            follow_redirects=False,
        )
        assert resp.status_code == 401


class TestTransitionDelete:
    def test_removes_existing_transition_record(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")
        test_client.post("/profile/transition", data=_valid_form(), follow_redirects=False)
        assert test_db.query(EmploymentTransition).filter(EmploymentTransition.user_id == test_user.id).first()

        resp = test_client.post("/profile/transition/delete", data={}, follow_redirects=False)

        assert resp.status_code == 302
        assert test_db.query(EmploymentTransition).filter(EmploymentTransition.user_id == test_user.id).first() is None

    def test_delete_with_no_existing_record_is_a_harmless_noop(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")

        resp = test_client.post("/profile/transition/delete", data={}, follow_redirects=False)

        assert resp.status_code == 302

    def test_cleanup_wage_removes_matching_wage_history_entry(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")
        t_date = datetime.date(2027, 6, 1)
        test_client.post(
            "/profile/transition",
            data=_valid_form(transition_date=t_date.isoformat(), new_direct_salary="40000"),
            follow_redirects=False,
        )
        wage = test_db.query(WageHistory).filter(WageHistory.user_id == test_user.id).first()
        assert wage is not None

        resp = test_client.post(
            "/profile/transition/delete",
            data={"cleanup_wage": "on"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        remaining = (
            test_db.query(WageHistory)
            .filter(WageHistory.user_id == test_user.id, WageHistory.effective_from == t_date)
            .first()
        )
        assert remaining is None

    def test_cleanup_rates_removes_entry_and_reopens_previous(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")
        t_date = datetime.date(2027, 6, 1)

        # Seed a rate entry that predates the transition, then one that starts on the
        # transition date (mirrors how the "reset rates to default" flow closes the
        # previous entry when a transition is saved).
        add_new_rates(test_db, test_user.id, {"ot": 50}, datetime.date(2020, 1, 1))
        add_new_rates(test_db, test_user.id, {"ot": 0}, t_date)

        test_client.post(
            "/profile/transition",
            data=_valid_form(transition_date=t_date.isoformat()),
            follow_redirects=False,
        )

        resp = test_client.post(
            "/profile/transition/delete",
            data={"cleanup_rates": "on"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        at_transition = (
            test_db.query(RateHistory)
            .filter(RateHistory.user_id == test_user.id, RateHistory.effective_from == t_date)
            .first()
        )
        assert at_transition is None

        previous = (
            test_db.query(RateHistory)
            .filter(RateHistory.user_id == test_user.id, RateHistory.effective_from == datetime.date(2020, 1, 1))
            .first()
        )
        assert previous is not None
        assert previous.effective_to is None

    def test_unauthenticated_delete_returns_401(self, test_client, test_db):
        resp = test_client.post("/profile/transition/delete", data={}, follow_redirects=False)
        assert resp.status_code == 401
