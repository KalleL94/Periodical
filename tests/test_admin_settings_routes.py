"""Integration tests for the admin settings update route.

Covers the validation-error path of POST /admin/settings, which historically
computed an "error" message but never rendered it in admin_settings.html,
silently dropping the feedback from the user (found while triaging #89).
"""


def _login(client, username, password):
    client.post("/login", data={"username": username, "password": password})


class TestAdminSettingsUpdateErrorDisplay:
    def test_invalid_monthly_salary_shows_error_message(self, test_client, test_db, admin_user):
        _login(test_client, "admin", "adminpass123")

        resp = test_client.post(
            "/admin/settings",
            data={
                "monthly_salary": 500,  # below the allowed 1000-1000000 range
                "person_wages": "{}",
            },
            follow_redirects=False,
        )

        assert resp.status_code == 400
        assert "Ogiltig månads lön" in resp.text
