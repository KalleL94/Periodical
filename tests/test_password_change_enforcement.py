"""Tests for mandatory password-change enforcement (audit item S2).

A user with must_change_password=1 must be redirected to /change-password on every
protected page, but must still be able to reach the change-password form and log out
(no redirect loop).
"""

import pytest

from app.auth.auth import (
    PasswordChangeRequired,
    _require_password_change,
    create_access_token,
    get_password_hash,
)
from app.database.database import User, UserRole


class _StubUser:
    def __init__(self, must_change_password: int):
        self.must_change_password = must_change_password


# --- Unit tests on the core enforcement helper -------------------------------


def test_require_password_change_raises_when_pending():
    with pytest.raises(PasswordChangeRequired):
        _require_password_change(_StubUser(1))


def test_require_password_change_passes_when_not_pending():
    # Should not raise
    _require_password_change(_StubUser(0))


def test_require_password_change_ignores_anonymous():
    # No authenticated user -> nothing to enforce
    _require_password_change(None)


# --- Integration tests via the HTTP layer ------------------------------------


def _make_user(db, must_change: int) -> User:
    user = User(
        username=f"user_mc{must_change}",
        password_hash=get_password_hash("irrelevant"),
        name="Test",
        role=UserRole.USER,
        wage=30000,
        vacation={},
        must_change_password=must_change,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _set_cookie(client, user: User) -> None:
    token = create_access_token(data={"sub": str(user.id)})
    client.cookies.set("access_token", f"Bearer {token}")


def test_protected_page_redirects_when_pending(test_client, test_db):
    user = _make_user(test_db, must_change=1)
    _set_cookie(test_client, user)

    resp = test_client.get("/", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/change-password"


def test_change_password_page_reachable_when_pending(test_client, test_db):
    """The change-password form itself must not redirect (no loop)."""
    user = _make_user(test_db, must_change=1)
    _set_cookie(test_client, user)

    resp = test_client.get("/change-password", follow_redirects=False)

    assert resp.status_code == 200


def test_logout_works_when_pending(test_client, test_db):
    """Logout must succeed for a pending user rather than redirecting to the form."""
    user = _make_user(test_db, must_change=1)
    _set_cookie(test_client, user)

    resp = test_client.get("/logout", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"
