"""Checks for the password hashing and JWT handling that replaced passlib and python-jose.

Two things matter here beyond "it round-trips". First, credentials issued by the
old libraries have to keep working: every password hash in the database was
written by passlib, and every live session cookie was signed by python-jose, so
a deploy that invalidated either would lock people out. Both are pinned below as
literals produced by the old libraries, so the check survives their removal.

Second, the token validator is now this repository's code, so the rejections are
worth testing as carefully as the acceptances.
"""

import base64
import calendar
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.auth import auth

# Issued by python-jose 3.5: HS256, sub "42", exp 2099-01-01.
LEGACY_JOSE_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiI0MiIsImV4cCI6NDA3MDkwODgwMH0"
    ".hStQfnxnkjoprtB3VFl_QO292oMNXnE3UlXrxv2UPOQ"
)
LEGACY_JOSE_SECRET = "test-secret-key-for-legacy-compatibility-checks"

# Written by passlib's CryptContext(schemes=["bcrypt"]) for this password.
LEGACY_PASSLIB_HASH = "$2b$12$i3dQ73nGGNY47sf/swgjGuw79P/ttYqE0nr0l1/wd/kN3EGwcZ6rm"
LEGACY_PASSLIB_PASSWORD = "correct horse battery staple"


@pytest.fixture
def secret(monkeypatch):
    """Sign and verify with the key the pinned legacy token was issued under."""
    monkeypatch.setattr(auth, "SECRET_KEY", LEGACY_JOSE_SECRET)
    return LEGACY_JOSE_SECRET


# --- passwords ---


def test_hash_written_by_passlib_still_verifies():
    assert auth.verify_password(LEGACY_PASSLIB_PASSWORD, LEGACY_PASSLIB_HASH) is True
    assert auth.verify_password("wrong", LEGACY_PASSLIB_HASH) is False


def test_hash_round_trip():
    hashed = auth.get_password_hash("hemligt lösenord åäö")
    assert auth.verify_password("hemligt lösenord åäö", hashed) is True
    assert auth.verify_password("hemligt lösenord åäo", hashed) is False


def test_hash_keeps_the_bcrypt_format_and_cost():
    # A cheaper cost factor would still verify, and nothing else would notice.
    assert auth.get_password_hash("x").startswith("$2b$12$")


def test_hashes_are_salted():
    assert auth.get_password_hash("same") != auth.get_password_hash("same")


def test_unparseable_stored_hash_is_a_failed_login_not_an_error():
    assert auth.verify_password("x", "not-a-bcrypt-hash") is False


# --- tokens ---


def test_token_issued_by_jose_still_validates(secret):
    assert auth.decode_token(LEGACY_JOSE_TOKEN) == {"sub": "42", "exp": 4070908800}


def test_token_round_trip(secret):
    assert auth.decode_token(auth.create_access_token({"sub": "7"}))["sub"] == "7"


def test_expiry_is_read_as_utc(secret):
    """utcnow() is naive UTC; reading it as local time would shift every expiry.

    The bug this pins is silent: tokens keep working, they just expire one or two
    hours off depending on the season.
    """
    token = auth.create_access_token({"sub": "7"}, expires_delta=timedelta(minutes=30))
    exp = auth.decode_token(token)["exp"]
    expected = calendar.timegm((datetime.now(UTC) + timedelta(minutes=30)).utctimetuple())
    assert abs(exp - expected) < 5


def test_expired_token_is_rejected(secret):
    assert auth.decode_token(auth.create_access_token({"sub": "7"}, expires_delta=timedelta(seconds=-1))) is None


def test_token_without_expiry_is_accepted(secret):
    """The tokens conftest issues for route tests carry no exp, as jose allowed."""
    header = auth._b64url_encode(auth._JWT_HEADER.encode())
    payload = auth._b64url_encode(b'{"sub":"7"}')
    signature = auth._b64url_encode(auth._jwt_signature(f"{header}.{payload}"))
    assert auth.decode_token(f"{header}.{payload}.{signature}") == {"sub": "7"}


def test_token_signed_with_another_key_is_rejected(secret, monkeypatch):
    token = auth.create_access_token({"sub": "7"})
    monkeypatch.setattr(auth, "SECRET_KEY", "a-different-deployment-secret")
    assert auth.decode_token(token) is None


def test_tampered_payload_is_rejected(secret):
    header, payload, signature = auth.create_access_token({"sub": "7"}).split(".")
    claims = json.loads(auth._b64url_decode(payload))
    claims["sub"] = "1"  # promote yourself to another user
    forged = auth._b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    assert auth.decode_token(f"{header}.{forged}.{signature}") is None


def test_alg_none_token_is_rejected(secret):
    """The classic JWT forgery: strip the signature and declare the token unsigned."""
    header = auth._b64url_encode(b'{"alg":"none","typ":"JWT"}')
    payload = auth._b64url_encode(b'{"sub":"1"}')
    assert auth.decode_token(f"{header}.{payload}.") is None


def test_non_string_sub_is_rejected(secret):
    """python-jose enforced this, so a token carrying an int sub was never valid."""
    header = auth._b64url_encode(auth._JWT_HEADER.encode())
    payload = auth._b64url_encode(b'{"sub":7}')
    signature = auth._b64url_encode(auth._jwt_signature(f"{header}.{payload}"))
    assert auth.decode_token(f"{header}.{payload}.{signature}") is None


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not-a-token",
        "only.two",
        "a.b.c.d",
        "!!!.!!!.!!!",
        base64.urlsafe_b64encode(b"[]").decode() + ".e30.x",
    ],
)
def test_malformed_tokens_are_rejected_without_raising(secret, token):
    assert auth.decode_token(token) is None
