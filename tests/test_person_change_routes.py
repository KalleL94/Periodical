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


class TestPersonChangePageGet:
    def test_renders_positions_with_holder_and_vacant(self, test_client, test_db, admin_user):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=admin_user.id)
        _login(test_client, "admin", "adminpass123")

        resp = test_client.get("/admin/person-change")

        assert resp.status_code == 200
        assert "Anna" in resp.text

    def test_requires_admin(self, test_client, test_db, test_user):
        _login(test_client, "testuser", "testpass123")
        resp = test_client.get("/admin/person-change", follow_redirects=False)
        assert resp.status_code in (302, 303, 401, 403)


class TestPersonChangePagePost:
    def _holder(self, test_db, admin_user):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=admin_user.id)
        return anna

    def test_swap_to_existing_user(self, test_client, test_db, admin_user):
        anna = self._holder(test_db, admin_user)
        bert = _make_user(test_db, 12, "bert1", "Bert")
        _login(test_client, "admin", "adminpass123")

        resp = test_client.post(
            "/admin/person-change",
            data={
                "person_id": 3,
                "last_working_day": "2026-03-31",
                "start_date": "2026-04-01",
                "successor_mode": "existing",
                "existing_user_id": str(bert.id),
            },
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert resp.headers["location"] == f"/admin/users/{bert.id}"
        test_db.expire_all()
        assert test_db.get(User, anna.id).is_active == 0
        assert test_db.get(User, bert.id).person_id == 3

    def test_swap_creating_new_user(self, test_client, test_db, admin_user):
        self._holder(test_db, admin_user)
        _login(test_client, "admin", "adminpass123")

        resp = test_client.post(
            "/admin/person-change",
            data={
                "person_id": 3,
                "last_working_day": "2026-03-31",
                "start_date": "2026-04-01",
                "successor_mode": "new",
                "new_name": "Casey New",
                "new_username": "casey1",
                "new_password": "secret123",
                "new_wage": "38000",
            },
            follow_redirects=False,
        )

        assert resp.status_code == 302
        test_db.expire_all()
        created = test_db.query(User).filter(User.username == "casey1").one()
        assert created.wage == 38000
        assert created.must_change_password == 1
        assert created.person_id == 3
        assert created.employment_start_date == datetime.date(2026, 4, 1)
        assert created.password_hash != "secret123"  # stored hashed

    def test_end_without_successor_leaves_vacancy(self, test_client, test_db, admin_user):
        anna = self._holder(test_db, admin_user)
        _login(test_client, "admin", "adminpass123")

        resp = test_client.post(
            "/admin/person-change",
            data={
                "person_id": 3,
                "last_working_day": "2026-03-31",
                "successor_mode": "none",
            },
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert "success=1" in resp.headers["location"]
        test_db.expire_all()
        open_count = (
            test_db.query(PersonHistory)
            .filter(PersonHistory.person_id == 3, PersonHistory.effective_to.is_(None))
            .count()
        )
        assert open_count == 0
        assert test_db.get(User, anna.id).is_active == 0

    def test_duplicate_username_returns_400(self, test_client, test_db, admin_user):
        self._holder(test_db, admin_user)
        _login(test_client, "admin", "adminpass123")

        resp = test_client.post(
            "/admin/person-change",
            data={
                "person_id": 3,
                "last_working_day": "2026-03-31",
                "start_date": "2026-04-01",
                "successor_mode": "new",
                "new_name": "Fake Admin",
                "new_username": "admin",
                "new_password": "secret123",
                "new_wage": "38000",
            },
            follow_redirects=False,
        )

        assert resp.status_code == 400
        # The swap must not have happened
        test_db.expire_all()
        open_rec = (
            test_db.query(PersonHistory)
            .filter(PersonHistory.person_id == 3, PersonHistory.effective_to.is_(None))
            .one()
        )
        assert open_rec.user_id == 11

    def test_missing_dates_returns_400(self, test_client, test_db, admin_user):
        self._holder(test_db, admin_user)
        bert = _make_user(test_db, 12, "bert1", "Bert")
        _login(test_client, "admin", "adminpass123")

        resp = test_client.post(
            "/admin/person-change",
            data={
                "person_id": 3,
                "successor_mode": "existing",
                "existing_user_id": str(bert.id),
            },
            follow_redirects=False,
        )

        assert resp.status_code == 400

    def test_vacant_position_with_no_successor_returns_400(self, test_client, test_db, admin_user):
        _login(test_client, "admin", "adminpass123")

        resp = test_client.post(
            "/admin/person-change",
            data={"person_id": 5, "last_working_day": "2026-03-31", "successor_mode": "none"},
            follow_redirects=False,
        )

        assert resp.status_code == 400
