"""Tests for username-enumeration hardening (audit item S3).

authenticate_user used to return immediately when the username did not exist, skipping the
(slow) bcrypt verification it runs for an existing user. That timing difference leaked which
usernames are valid. It must now run a dummy verification for a missing user too.

Timing itself is too flaky to assert, so we verify the behaviour: a missing user still
triggers exactly one password verification.
"""

from app.auth import auth
from app.database.database import User, UserRole


def test_unknown_user_still_runs_a_verification(test_db, monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "verify_password", lambda pw, h: calls.append(h) or False)

    result = auth.authenticate_user(test_db, "no-such-user", "whatever")

    assert result is None
    # A dummy verify ran despite the user not existing (equalises timing).
    assert len(calls) == 1
    assert calls[0] == auth._DUMMY_PASSWORD_HASH


def test_valid_credentials_authenticate(test_db):
    user = User(
        username="realuser",
        password_hash=auth.get_password_hash("correct horse"),
        name="Real",
        role=UserRole.USER,
        wage=30000,
        vacation={},
        must_change_password=0,
    )
    test_db.add(user)
    test_db.commit()

    assert auth.authenticate_user(test_db, "realuser", "correct horse") is not None


def test_wrong_password_fails(test_db):
    user = User(
        username="realuser2",
        password_hash=auth.get_password_hash("correct horse"),
        name="Real",
        role=UserRole.USER,
        wage=30000,
        vacation={},
        must_change_password=0,
    )
    test_db.add(user)
    test_db.commit()

    assert auth.authenticate_user(test_db, "realuser2", "wrong") is None
