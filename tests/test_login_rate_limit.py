"""Tests for login brute-force protection (audit item S1).

After LOGIN_MAX_ATTEMPTS failed attempts for the same (username, ip) within the window,
further attempts are rejected with 429 until they age out; a successful login clears them.
"""

from datetime import timedelta

from app.auth.auth import (
    LOGIN_MAX_ATTEMPTS,
    LOGIN_WINDOW_MINUTES,
    clear_login_attempts,
    is_login_locked,
    record_failed_login,
)
from app.database.database import LoginAttempt, utcnow

IP = "1.2.3.4"


# --- Unit tests on the rate-limit helpers ------------------------------------


def test_locks_after_max_attempts(test_db):
    for _ in range(LOGIN_MAX_ATTEMPTS):
        record_failed_login(test_db, "bob", IP)

    assert is_login_locked(test_db, "bob", IP) is True
    # A different IP and a different username share neither the count nor the lock.
    assert is_login_locked(test_db, "bob", "9.9.9.9") is False
    assert is_login_locked(test_db, "alice", IP) is False


def test_below_threshold_not_locked(test_db):
    for _ in range(LOGIN_MAX_ATTEMPTS - 1):
        record_failed_login(test_db, "bob", IP)

    assert is_login_locked(test_db, "bob", IP) is False


def test_clear_attempts_unlocks(test_db):
    for _ in range(LOGIN_MAX_ATTEMPTS):
        record_failed_login(test_db, "bob", IP)
    clear_login_attempts(test_db, "bob", IP)

    assert is_login_locked(test_db, "bob", IP) is False


def test_attempts_outside_window_do_not_count(test_db):
    old = utcnow() - timedelta(minutes=LOGIN_WINDOW_MINUTES + 1)
    for _ in range(LOGIN_MAX_ATTEMPTS):
        test_db.add(LoginAttempt(username="bob", ip=IP, created_at=old))
    test_db.commit()

    assert is_login_locked(test_db, "bob", IP) is False


# --- Integration tests via the login route -----------------------------------


def _post_login(client, password):
    return client.post(
        "/login",
        data={"username": "testuser", "password": password},
        follow_redirects=False,
    )


def test_login_blocks_after_max_failures(test_client, test_user):
    for _ in range(LOGIN_MAX_ATTEMPTS):
        resp = _post_login(test_client, "wrongpass")
        assert resp.status_code == 401

    # Even the correct password is now rejected with 429 (locked).
    resp = _post_login(test_client, "testpass123")
    assert resp.status_code == 429


def test_successful_login_resets_counter(test_client, test_user, test_db):
    for _ in range(LOGIN_MAX_ATTEMPTS - 1):
        _post_login(test_client, "wrongpass")

    resp = _post_login(test_client, "testpass123")
    assert resp.status_code == 302  # success redirect
    assert test_db.query(LoginAttempt).count() == 0
