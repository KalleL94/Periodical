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


class TestCalendarFeedLanguage:
    """The feed renders in the user's stored language, not a hardcoded one.

    Rendering per language is covered by test_calendar_export; here we verify
    the endpoint threads User.language into the generator. We spy on the
    generator (rather than seed a rotation era) so the assertion is about the
    wiring, independent of whether the feed window contains any shifts.
    """

    def _give_token(self, test_db, test_user, token="feed-token-lang"):
        test_user.calendar_token = hash_api_key(token)
        test_user.calendar_token_encrypted = encrypt_api_key(token)
        test_db.commit()
        return token

    def _spy_lang(self, monkeypatch):
        captured = {}

        def fake_generate(user, start_date, end_date, lang="sv", session=None, as_feed=False):
            captured["lang"] = lang
            return "BEGIN:VCALENDAR\nEND:VCALENDAR\n"

        import app.routes.calendar_feed as feed_module

        monkeypatch.setattr(feed_module, "generate_ical_for_user", fake_generate)
        return captured

    def test_feed_uses_english_for_english_user(self, test_client, test_db, test_user, monkeypatch):
        token = self._give_token(test_db, test_user)
        test_user.language = "en"
        test_db.commit()
        captured = self._spy_lang(monkeypatch)

        response = test_client.get(f"/calendar/feed/{token}/schema.ics")

        assert response.status_code == 200
        assert captured["lang"] == "en"

    def test_feed_uses_swedish_for_swedish_user(self, test_client, test_db, test_user, monkeypatch):
        token = self._give_token(test_db, test_user)
        test_user.language = "sv"
        test_db.commit()
        captured = self._spy_lang(monkeypatch)

        response = test_client.get(f"/calendar/feed/{token}/schema.ics")

        assert response.status_code == 200
        assert captured["lang"] == "sv"


class TestCalendarTokenLifecycle:
    def test_generate_stores_hash_and_encrypted_copy(self, test_client, test_user, test_db):
        _login(test_client)

        response = test_client.post("/profile/calendar-token/generate", follow_redirects=False)

        assert response.status_code == 302
        test_db.refresh(test_user)
        assert test_user.calendar_token is not None
        assert len(test_user.calendar_token) == 64
        assert test_user.calendar_token_encrypted is not None

    def test_rotation_invalidates_old_token(self, test_client, test_user, test_db):
        _login(test_client)
        test_client.post("/profile/calendar-token/generate", follow_redirects=False)
        test_db.refresh(test_user)
        old_hash = test_user.calendar_token

        test_client.post("/profile/calendar-token/generate", follow_redirects=False)
        test_db.refresh(test_user)

        assert test_user.calendar_token != old_hash

    def test_revoke_clears_both_columns(self, test_client, test_user, test_db):
        _login(test_client)
        test_client.post("/profile/calendar-token/generate", follow_redirects=False)

        response = test_client.post("/profile/calendar-token/revoke", follow_redirects=False)

        assert response.status_code == 302
        test_db.refresh(test_user)
        assert test_user.calendar_token is None
        assert test_user.calendar_token_encrypted is None

    def test_generate_requires_login(self, test_client, test_user, test_db):
        response = test_client.post("/profile/calendar-token/generate", follow_redirects=False)

        assert response.status_code in (302, 401)
        test_db.refresh(test_user)
        assert test_user.calendar_token is None


class TestProfilePageRendering:
    def test_profile_shows_webcal_url_when_token_exists(self, test_client, test_user, test_db):
        _login(test_client)
        test_client.post("/profile/calendar-token/generate", follow_redirects=False)

        response = test_client.get("/profile")

        assert response.status_code == 200
        assert "webcal://" in response.text
        assert "/calendar/feed/" in response.text

    def test_profile_renders_without_token(self, test_client, test_user, test_db):
        _login(test_client)

        response = test_client.get("/profile")

        assert response.status_code == 200
        assert "webcal://" not in response.text
