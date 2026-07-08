"""Integration tests for employment admin routes and the person-change page.

Exercises the full HTTP path: form parsing, domain validation surfaced as
form errors, PRG redirects, cache invalidation, and DB mutations.
"""

import datetime

from app.core.schedule.person_history import start_employment
from app.database.database import PersonHistory, User, UserRole


def _login(client, username, password):
    client.post("/login", data={"username": username, "password": password})


def _make_user(test_db, uid, username, name):
    user = User(
        id=uid,
        username=username,
        password_hash="x",
        name=name,
        role=UserRole.USER,
        wage=30000,
        vacation={},
        must_change_password=0,
        is_active=0,
    )
    test_db.add(user)
    test_db.commit()
    return user


class TestExistingEmploymentRoutes:
    def test_start_employment_on_occupied_position_returns_400(self, test_client, test_db, admin_user):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        bert = _make_user(test_db, 12, "bert1", "Bert")
        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=admin_user.id)
        _login(test_client, "admin", "adminpass123")

        resp = test_client.post(
            f"/admin/users/{bert.id}/start-employment",
            data={"person_id": 3, "start_date": "2026-02-01"},
            follow_redirects=False,
        )

        assert resp.status_code == 400
        open_count = (
            test_db.query(PersonHistory)
            .filter(PersonHistory.person_id == 3, PersonHistory.effective_to.is_(None))
            .count()
        )
        assert open_count == 1

    def test_start_employment_clears_schedule_cache(self, test_client, test_db, admin_user, monkeypatch):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        _login(test_client, "admin", "adminpass123")

        calls = []
        monkeypatch.setattr("app.routes.admin_users.clear_schedule_cache", lambda: calls.append(1))

        resp = test_client.post(
            f"/admin/users/{anna.id}/start-employment",
            data={"person_id": 3, "start_date": "2026-02-01"},
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert calls

    def test_end_employment_clears_schedule_cache(self, test_client, test_db, admin_user, monkeypatch):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=admin_user.id)
        _login(test_client, "admin", "adminpass123")

        calls = []
        monkeypatch.setattr("app.routes.admin_users.clear_schedule_cache", lambda: calls.append(1))

        resp = test_client.post(
            f"/admin/users/{anna.id}/end-employment",
            data={"person_id": 3, "end_date": "2026-03-31"},
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert calls

    def test_end_employment_before_start_returns_400(self, test_client, test_db, admin_user):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=admin_user.id)
        _login(test_client, "admin", "adminpass123")

        resp = test_client.post(
            f"/admin/users/{anna.id}/end-employment",
            data={"person_id": 3, "end_date": "2025-06-01"},
            follow_redirects=False,
        )

        assert resp.status_code == 400
