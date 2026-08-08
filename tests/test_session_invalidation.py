"""Changing a password must end the sessions that were already open (issue #331).

Before this, `change_password` wrote the new hash and committed, and that was the
whole of it. Tokens are stateless and carried nothing to compare against, so a
stolen cookie kept full access for the remaining seven days of its lifetime. The
one remediation a user can perform for themselves did nothing.

The mechanism is `iat` on the token against `users.password_changed_at`. The
comparison is at second granularity on both sides (`calendar.timegm` of a
`utctimetuple`), so these tests set `password_changed_at` explicitly rather than
racing the wall clock.
"""

import calendar
from datetime import timedelta

from app.auth.auth import create_access_token, decode_token, set_password
from app.database.database import User, utcnow


def _client_with_token(client, user):
    client.cookies.set("access_token", f"Bearer {create_access_token(data={'sub': str(user.id)})}")
    return client


def _stamp(user, db, when):
    user.password_changed_at = when
    db.commit()


# ============ The token carries when it was issued ============


def test_every_token_records_its_issue_time():
    payload = decode_token(create_access_token(data={"sub": "1"}))
    now = calendar.timegm(utcnow().utctimetuple())
    assert abs(payload["iat"] - now) < 5


# ============ The comparison ============


def test_a_token_issued_before_the_change_is_refused(test_client, test_db, test_user):
    _client_with_token(test_client, test_user)
    assert test_client.get("/profile", follow_redirects=False).status_code == 200

    _stamp(test_user, test_db, utcnow() + timedelta(seconds=60))

    # 401: the dependency resolves the cookie to no user at all, which is the
    # same answer an unauthenticated request gets. The session is gone.
    assert test_client.get("/profile", follow_redirects=False).status_code == 401


def test_a_token_issued_after_the_change_still_works(test_client, test_db, test_user):
    _stamp(test_user, test_db, utcnow() - timedelta(seconds=60))
    _client_with_token(test_client, test_user)

    assert test_client.get("/profile", follow_redirects=False).status_code == 200


def test_sessions_live_at_deploy_time_survive(test_client, test_db, test_user):
    """password_changed_at is NULL until someone actually changes a password.

    Existing cookies carry no `iat` either, so the deploy that introduces this
    must not log the whole team out. That is the case this pins.
    """
    assert test_user.password_changed_at is None

    header, payload, _ = create_access_token(data={"sub": str(test_user.id)}).split(".")
    import json

    from app.auth.auth import _b64url_decode, _b64url_encode, _jwt_signature

    claims = json.loads(_b64url_decode(payload))
    del claims["iat"]  # a token minted by the previous release
    legacy_payload = _b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    signature = _b64url_encode(_jwt_signature(f"{header}.{legacy_payload}"))
    test_client.cookies.set("access_token", f"Bearer {header}.{legacy_payload}.{signature}")

    assert test_client.get("/profile", follow_redirects=False).status_code == 200


def test_a_token_without_an_issue_time_is_refused_once_a_password_has_changed(test_client, test_db, test_user):
    """There is no way to tell whether it predates the change, so it does not pass."""
    header, payload, _ = create_access_token(data={"sub": str(test_user.id)}).split(".")
    import json

    from app.auth.auth import _b64url_decode, _b64url_encode, _jwt_signature

    claims = json.loads(_b64url_decode(payload))
    del claims["iat"]
    legacy_payload = _b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    signature = _b64url_encode(_jwt_signature(f"{header}.{legacy_payload}"))
    test_client.cookies.set("access_token", f"Bearer {header}.{legacy_payload}.{signature}")

    _stamp(test_user, test_db, utcnow())

    assert test_client.get("/profile", follow_redirects=False).status_code == 401


# ============ The routes that change a password set the stamp ============


def test_changing_your_own_password_sets_the_stamp(test_client, test_db, test_user):
    _client_with_token(test_client, test_user)

    resp = test_client.post(
        "/profile/password",
        data={"current_password": "testpass123", "new_password": "a-brand-new-password"},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    test_db.expire_all()
    assert test_db.query(User).filter(User.id == test_user.id).one().password_changed_at is not None


def test_an_admin_reset_sets_the_stamp(test_client, test_db, admin_user, test_user):
    test_client.post("/login", data={"username": "admin", "password": "adminpass123"})

    resp = test_client.post(f"/admin/users/{test_user.id}/reset-password", follow_redirects=False)

    assert resp.status_code == 302
    test_db.expire_all()
    assert test_db.query(User).filter(User.id == test_user.id).one().password_changed_at is not None


def test_set_password_stamps_and_rehashes(test_db, test_user):
    from app.auth.auth import verify_password

    set_password(test_user, "another-password")
    test_db.commit()

    assert verify_password("another-password", test_user.password_hash)
    assert test_user.password_changed_at is not None


# ============ API keys are not sessions ============


def test_an_api_key_keeps_working_after_a_password_change(test_client, test_db, test_user):
    """The key is a separate credential with its own lifecycle; a password change
    is not a statement about it, and revoking it has its own button.

    The assertion is "not 401" rather than "200" on purpose. 401 is what a
    rejected credential looks like and it is the only outcome this test is
    about; what the endpoint answers once it has authenticated depends on
    whether there is schedule data behind it, which is a different test's job.
    Asserting 200 passed locally against a populated development database and
    failed in CI against an empty one, which is the test being wrong rather than
    the code.
    """
    from app.auth.auth import hash_api_key

    test_user.api_key = hash_api_key("a-real-api-key")
    _stamp(test_user, test_db, utcnow() + timedelta(seconds=60))

    url = f"/api/v1/users/{test_user.id}/next-shift"
    accepted = test_client.get(url, headers={"Authorization": "Bearer a-real-api-key"})
    rejected = test_client.get(url, headers={"Authorization": "Bearer not-the-key"})

    # The pair is the point: 401 is reachable on this endpoint, and the real key
    # does not get it even though the password changed a minute into the future.
    assert rejected.status_code == 401
    assert accepted.status_code != 401
