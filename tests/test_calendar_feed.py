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

from app.auth.auth import hash_api_key
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
