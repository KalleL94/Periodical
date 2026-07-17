"""
Tests for the token-authenticated ICS subscription feed.

The calendar token mirrors the API key storage pattern: SHA-256 hash for
lookups (User.calendar_token) plus a Fernet-encrypted copy for display
(User.calendar_token_encrypted). The plaintext token is never persisted.
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ruff: noqa: E402

from app.auth.auth import encrypt_api_key, hash_api_key
from app.database.database import User


def _login(test_client):
    test_client.post(
        "/login",
        data={"username": "testuser", "password": "testpass123"},
    )


class TestCalendarTokenColumns:
    def test_user_has_calendar_token_columns(self, test_db, test_user):
        test_user.calendar_token = hash_api_key("some-token")
        test_user.calendar_token_encrypted = "encrypted-blob"
        test_db.commit()

        # Query via klasskolumnen - failar med AttributeError innan kolumnen finns.
        fetched = test_db.query(User).filter(User.calendar_token == hash_api_key("some-token")).one()
        assert fetched.id == test_user.id
        assert fetched.calendar_token_encrypted == "encrypted-blob"


class TestCalendarFeedEndpoint:
    def _give_token(self, test_db, test_user, token="feed-token-abc"):
        test_user.calendar_token = hash_api_key(token)
        test_user.calendar_token_encrypted = encrypt_api_key(token)
        test_db.commit()
        return token

    def test_valid_token_returns_calendar(self, test_client, test_db, test_user):
        token = self._give_token(test_db, test_user)

        response = test_client.get(f"/calendar/feed/{token}/schema.ics")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/calendar")
        assert "attachment" not in response.headers.get("content-disposition", "")
        assert "BEGIN:VCALENDAR" in response.text
        assert "REFRESH-INTERVAL" in response.text

    def test_unknown_token_returns_404(self, test_client, test_db, test_user):
        self._give_token(test_db, test_user)

        response = test_client.get("/calendar/feed/wrong-token/schema.ics")

        assert response.status_code == 404

    def test_revoked_token_returns_404(self, test_client, test_db, test_user):
        token = self._give_token(test_db, test_user)
        test_user.calendar_token = None
        test_user.calendar_token_encrypted = None
        test_db.commit()

        response = test_client.get(f"/calendar/feed/{token}/schema.ics")

        assert response.status_code == 404

    def test_no_login_required(self, test_client, test_db, test_user):
        # Ingen _login() - feeden är sessionlös per design.
        token = self._give_token(test_db, test_user)

        response = test_client.get(f"/calendar/feed/{token}/schema.ics")

        assert response.status_code == 200
