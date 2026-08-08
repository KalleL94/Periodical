"""The reset password must be generated, never a constant.

It used to be `DEFAULT_PASSWORD = "London1"` in app/core/constants.py, committed
to a public repository. Anyone reading the source knew the password every reset
account was sitting on, and the login rate limiter allows five attempts per
username, so one guess was free. The reset also sets must_change_password, but
that does not help: whoever gets in first holds the current password and can
complete the forced change themselves.

So the property worth pinning is that two resets never produce the same
password, which a reintroduced constant would fail immediately.
"""

import re

from app.auth.auth import verify_password
from app.database.database import User


def _login(client, username, password):
    client.post("/login", data={"username": username, "password": password})


def _reset(client, user_id):
    """Reset the user's password and return the one the response reveals."""
    resp = client.post(f"/admin/users/{user_id}/reset-password", follow_redirects=False)
    assert resp.status_code == 302
    match = re.search(r"terst%C3%A4llts%20till%20(\S+)$", resp.headers["location"])
    assert match, f"password not found in redirect: {resp.headers['location']}"
    return match.group(1)


def test_each_reset_generates_a_different_password(test_client, test_db, admin_user, test_user):
    _login(test_client, "admin", "adminpass123")

    first = _reset(test_client, test_user.id)
    second = _reset(test_client, test_user.id)

    assert first != second, "reset password is a constant, not generated"
    assert len(first) >= 16, f"generated password is too short: {len(first)} chars"


def test_the_revealed_password_is_the_one_that_was_stored(test_client, test_db, admin_user, test_user):
    """A password shown to the admin that does not work is worse than useless."""
    _login(test_client, "admin", "adminpass123")

    password = _reset(test_client, test_user.id)

    test_db.expire_all()
    reset_user = test_db.query(User).filter(User.id == test_user.id).one()
    assert verify_password(password, reset_user.password_hash)
    assert reset_user.must_change_password == 1
