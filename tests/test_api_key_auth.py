"""
Tests for API key hashing and encrypted at-rest storage.

API keys are stored as a SHA-256 hash (for authentication lookups) plus a
Fernet-encrypted copy (so the profile page can display the key). The plaintext
key must never be persisted.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ruff: noqa: E402

from app.auth.auth import decrypt_api_key, encrypt_api_key, hash_api_key


def _login(test_client):
    test_client.post(
        "/login",
        data={"username": "testuser", "password": "testpass123"},
    )


class TestApiKeyHelpers:
    """Unit tests for the hash/encrypt helpers."""

    def test_hash_is_deterministic_sha256_hex(self):
        digest = hash_api_key("some-key")

        assert digest == hash_api_key("some-key")
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)
        assert digest != "some-key"

    def test_encrypt_decrypt_roundtrip(self):
        token = encrypt_api_key("some-key")

        assert token != "some-key"
        assert decrypt_api_key(token) == "some-key"

    def test_decrypt_returns_none_for_missing_or_invalid_token(self):
        assert decrypt_api_key(None) is None
        assert decrypt_api_key("") is None
        assert decrypt_api_key("not-a-fernet-token") is None


class TestApiKeyGeneration:
    """Tests for the generate/revoke flow on the profile page."""

    def test_generate_stores_hash_and_encrypted_copy(self, test_client, test_user, test_db):
        _login(test_client)

        response = test_client.post("/profile/api-key/generate", follow_redirects=False)

        assert response.status_code == 302
        test_db.refresh(test_user)
        plaintext = decrypt_api_key(test_user.api_key_encrypted)
        assert plaintext is not None
        # Stored lookup value is the hash of the key, never the plaintext
        assert test_user.api_key == hash_api_key(plaintext)
        assert test_user.api_key != plaintext

    def test_profile_page_displays_key_on_repeat_visits(self, test_client, test_user, test_db):
        _login(test_client)
        test_client.post("/profile/api-key/generate", follow_redirects=False)
        test_db.refresh(test_user)
        plaintext = decrypt_api_key(test_user.api_key_encrypted)

        for _ in range(2):
            response = test_client.get("/profile")
            assert response.status_code == 200
            assert plaintext in response.text

    def test_revoke_clears_both_columns(self, test_client, test_user, test_db):
        _login(test_client)
        test_client.post("/profile/api-key/generate", follow_redirects=False)

        response = test_client.post("/profile/api-key/revoke", follow_redirects=False)

        assert response.status_code == 302
        test_db.refresh(test_user)
        assert test_user.api_key is None
        assert test_user.api_key_encrypted is None


class TestApiKeyAuthentication:
    """Tests for Bearer API key authentication against /api/v1."""

    def test_generated_key_authenticates(self, test_client, test_user, test_db):
        _login(test_client)
        test_client.post("/profile/api-key/generate", follow_redirects=False)
        test_db.refresh(test_user)
        plaintext = decrypt_api_key(test_user.api_key_encrypted)

        response = test_client.get("/api/v1/me", headers={"Authorization": f"Bearer {plaintext}"})

        assert response.status_code == 200
        assert response.json()["username"] == "testuser"

    def test_stored_hash_does_not_authenticate(self, test_client, test_user, test_db):
        """The stored hash must not be usable as a key (a DB leak stays harmless)."""
        _login(test_client)
        test_client.post("/profile/api-key/generate", follow_redirects=False)
        test_db.refresh(test_user)

        response = test_client.get("/api/v1/me", headers={"Authorization": f"Bearer {test_user.api_key}"})

        assert response.status_code == 401

    def test_invalid_key_rejected(self, test_client, test_user):
        response = test_client.get("/api/v1/me", headers={"Authorization": "Bearer wrong-key"})

        assert response.status_code == 401

    def test_missing_key_rejected(self, test_client, test_user):
        response = test_client.get("/api/v1/me")

        assert response.status_code == 401
