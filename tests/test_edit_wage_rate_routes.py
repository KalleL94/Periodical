"""Integration tests for the profile/admin edit-wage and edit-rate routes.

These exercise the full HTTP path: form parsing, ownership checks, the
POST-Redirect-Get response, and the resulting DB mutation. The edit routes
change only the value on an existing history record; the effective_from /
effective_to dates must stay untouched.
"""

import datetime

from app.core.rates import add_new_rates
from app.core.schedule.wages import add_new_wage
from app.database.database import RateHistory


def _login(client, username, password):
    client.post("/login", data={"username": username, "password": password})


class TestProfileEditWage:
    def test_edit_wage_updates_value_and_keeps_dates(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")
        rec = add_new_wage(test_db, test_user.id, 35000, datetime.date(2024, 1, 1))
        from_before, to_before = rec.effective_from, rec.effective_to

        resp = test_client.post(
            f"/profile/edit-wage/{rec.id}",
            data={"new_wage": 42000},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        test_db.refresh(rec)
        assert rec.wage == 42000
        assert rec.effective_from == from_before
        assert rec.effective_to == to_before

    def test_edit_wage_of_other_user_is_forbidden(self, test_client, test_db, test_user, admin_user):
        # A record owned by the admin cannot be edited via the current user's profile route.
        _login(test_client, "testuser", "testpass123")
        rec = add_new_wage(test_db, admin_user.id, 45000, datetime.date(2024, 1, 1))

        resp = test_client.post(
            f"/profile/edit-wage/{rec.id}",
            data={"new_wage": 99999},
            follow_redirects=False,
        )
        assert resp.status_code == 403
        test_db.refresh(rec)
        assert rec.wage == 45000


class TestProfileEditRate:
    def test_edit_rate_updates_values_and_keeps_dates(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")
        add_new_rates(test_db, test_user.id, {"ot": 50}, datetime.date(2024, 1, 1))
        rec = test_db.query(RateHistory).filter(RateHistory.user_id == test_user.id).first()
        from_before, to_before = rec.effective_from, rec.effective_to

        resp = test_client.post(
            f"/profile/edit-rate/{rec.id}",
            data={"rate_ot": "75", "rate_ob_OB1": "30"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        test_db.refresh(rec)
        assert rec.rates["ot"] == 75
        assert rec.rates["ob"]["OB1"] == 30
        assert rec.effective_from == from_before
        assert rec.effective_to == to_before


class TestAdminEditWage:
    def test_admin_can_edit_any_users_wage(self, test_client, test_db, admin_user, test_user):
        _login(test_client, "admin", "adminpass123")
        rec = add_new_wage(test_db, test_user.id, 35000, datetime.date(2024, 1, 1))

        resp = test_client.post(
            f"/admin/users/{test_user.id}/edit-wage/{rec.id}",
            data={"new_wage": 38000},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        test_db.refresh(rec)
        assert rec.wage == 38000

    def test_admin_edit_wage_wrong_user_rejected(self, test_client, test_db, admin_user, test_user):
        # Record belongs to test_user but the path names the admin -> mismatch rejected.
        _login(test_client, "admin", "adminpass123")
        rec = add_new_wage(test_db, test_user.id, 35000, datetime.date(2024, 1, 1))

        resp = test_client.post(
            f"/admin/users/{admin_user.id}/edit-wage/{rec.id}",
            data={"new_wage": 1},
            follow_redirects=False,
        )
        assert resp.status_code == 400
        test_db.refresh(rec)
        assert rec.wage == 35000


class TestAdminEditRate:
    def test_admin_can_edit_any_users_rate(self, test_client, test_db, admin_user, test_user):
        _login(test_client, "admin", "adminpass123")
        add_new_rates(test_db, test_user.id, {"ot": 40}, datetime.date(2024, 1, 1))
        rec = test_db.query(RateHistory).filter(RateHistory.user_id == test_user.id).first()

        resp = test_client.post(
            f"/admin/users/{test_user.id}/edit-rate/{rec.id}",
            data={"rate_ot": "55"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        test_db.refresh(rec)
        assert rec.rates["ot"] == 55
