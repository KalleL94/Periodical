"""Integration tests for POST /admin/users/{id}.

The short-password branch used to render admin_user_edit.html with a four-key
context, so Jinja raised on the template's salary_types loop and the 400 turned
into a 500. The route also skipped clear_schedule_cache() even though it writes
person_id, which decides the user's rotation.
"""

from app.database.database import User


def _login(client, username, password):
    client.post("/login", data={"username": username, "password": password})


class TestAdminUpdateUser:
    def test_short_password_renders_the_error_page_not_a_500(self, test_client, test_db, admin_user):
        _login(test_client, "admin", "adminpass123")

        resp = test_client.post(
            f"/admin/users/{admin_user.id}",
            data={"name": "Admin", "role": "admin", "new_password": "short"},
            follow_redirects=False,
        )

        assert resp.status_code == 400
        assert "minst 8 tecken" in resp.text

    def test_rejected_update_leaves_the_user_untouched(self, test_client, test_db, admin_user):
        _login(test_client, "admin", "adminpass123")
        original_hash = admin_user.password_hash

        test_client.post(
            f"/admin/users/{admin_user.id}",
            data={"name": "Renamed", "role": "admin", "new_password": "short"},
            follow_redirects=False,
        )

        test_db.expire_all()
        stored = test_db.query(User).filter(User.id == admin_user.id).first()
        assert stored.name == "Admin User"
        assert stored.password_hash == original_hash

    def test_person_id_change_clears_the_schedule_cache(self, test_client, test_db, admin_user, monkeypatch):
        _login(test_client, "admin", "adminpass123")
        calls = []
        monkeypatch.setattr("app.routes.admin_users.clear_schedule_cache", lambda: calls.append(1))

        resp = test_client.post(
            f"/admin/users/{admin_user.id}",
            data={"name": "Admin", "role": "admin", "person_id": 3},
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert calls, "person_id decides the rotation, so cached periods must be dropped"
