"""Usernames identify a person, so their capitalisation must not matter.

Logging in as Testuser or TESTUSER reaches the same account as testuser, the
same spelling can not be registered twice under a different case, and the
brute-force limiter counts every spelling toward one bucket.
"""

from app.auth.auth import (
    LOGIN_MAX_ATTEMPTS,
    authenticate_user,
    get_user_by_username,
    is_login_locked,
    record_failed_login,
)

IP = "1.2.3.4"


def test_lookup_ignores_case(test_db, test_user):
    for spelling in ("testuser", "Testuser", "TESTUSER", "TestUser"):
        found = get_user_by_username(test_db, spelling)
        assert found is not None, spelling
        assert found.id == test_user.id


def test_lookup_ignores_surrounding_whitespace(test_db, test_user):
    assert get_user_by_username(test_db, "  TestUser  ").id == test_user.id


def test_stored_spelling_is_preserved(test_db, test_user):
    assert get_user_by_username(test_db, "TESTUSER").username == "testuser"


def test_authentication_accepts_any_case(test_db, test_user):
    assert authenticate_user(test_db, "TestUser", "testpass123").id == test_user.id


def test_password_stays_case_sensitive(test_db, test_user):
    assert authenticate_user(test_db, "testuser", "TESTPASS123") is None


def test_unknown_username_still_returns_nothing(test_db, test_user):
    assert get_user_by_username(test_db, "nosuchuser") is None


def test_rate_limit_counts_every_spelling_in_one_bucket(test_db):
    spellings = ("bob", "Bob", "BOB", "bOb", "BoB")
    assert len(spellings) == LOGIN_MAX_ATTEMPTS
    for spelling in spellings:
        record_failed_login(test_db, spelling, IP)

    assert is_login_locked(test_db, "bob", IP) is True
    assert is_login_locked(test_db, "BOB", IP) is True
