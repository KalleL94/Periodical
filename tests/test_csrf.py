"""Tests for CSRF protection (issue #156).

Covers the token primitives in app/auth/csrf.py and the end-to-end middleware
behaviour: browser form POSTs require a valid double-submit token, while
header-authenticated API traffic stays exempt.
"""

import pytest

from app.auth.auth import create_access_token
from app.auth.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_FIELD_NAME,
    generate_csrf_token,
    is_signed_token,
    tokens_match,
)


def _set_auth_cookie(client, user) -> None:
    """Authenticate the test client as `user` (mirrors the app's cookie auth)."""
    token = create_access_token(data={"sub": str(user.id)})
    client.cookies.set("access_token", f"Bearer {token}")


# ============ Token primitives ============


def test_generated_token_is_signed():
    assert is_signed_token(generate_csrf_token())


def test_generated_tokens_are_unique():
    assert generate_csrf_token() != generate_csrf_token()


@pytest.mark.parametrize(
    "bogus",
    ["", "no-dot-separator", "nonce.badsignature", ".", "nonce.", ".signature"],
)
def test_malformed_tokens_are_rejected(bogus):
    assert not is_signed_token(bogus)


def test_token_with_tampered_nonce_is_rejected():
    """An attacker who edits the nonce cannot keep the signature valid."""
    nonce, _, signature = generate_csrf_token().partition(".")
    assert not is_signed_token(f"{nonce}tampered.{signature}")


def test_tokens_match_requires_both_values():
    token = generate_csrf_token()
    assert not tokens_match(None, token)
    assert not tokens_match(token, None)
    assert not tokens_match("", "")


def test_tokens_match_requires_equal_and_signed_values():
    token = generate_csrf_token()
    assert tokens_match(token, token)
    assert not tokens_match(token, generate_csrf_token())


def test_tokens_match_rejects_unsigned_cookie():
    """A cookie injected by a subdomain attacker carries no valid signature."""
    forged = "attacker-chosen-value"
    assert not tokens_match(forged, forged)


# ============ Middleware: safe methods ============


def test_get_request_sets_csrf_cookie(raw_client):
    """Rendering any page seeds the double-submit cookie."""
    response = raw_client.get("/login")

    assert response.status_code == 200
    assert is_signed_token(response.cookies[CSRF_COOKIE_NAME])


def test_existing_valid_cookie_is_not_rotated(raw_client):
    """A stable token keeps already-rendered forms in other tabs valid."""
    token = generate_csrf_token()
    raw_client.cookies.set(CSRF_COOKIE_NAME, token)

    raw_client.get("/login")

    assert raw_client.cookies[CSRF_COOKIE_NAME] == token


def test_invalid_cookie_is_replaced_on_safe_request(raw_client):
    """A tampered or stale cookie is reissued rather than left in place.

    Asserted on the response header rather than the client jar: httpx raises
    CookieConflict when the pre-set and reissued cookies differ in path.
    """
    raw_client.cookies.set(CSRF_COOKIE_NAME, "garbage")

    response = raw_client.get("/login")

    set_cookie = response.headers["set-cookie"]
    assert CSRF_COOKIE_NAME in set_cookie
    issued = set_cookie.split(f"{CSRF_COOKIE_NAME}=")[1].split(";")[0]
    assert is_signed_token(issued)


# ============ Middleware: rejection of unprotected POSTs ============


def test_post_without_token_is_rejected(raw_client, test_user):
    _set_auth_cookie(raw_client, test_user)

    response = raw_client.post("/profile/language", data={"lang": "en"})

    assert response.status_code == 403


def test_post_with_mismatched_token_is_rejected(raw_client, test_user):
    """The core CSRF defence: a cross-site form cannot read our cookie."""
    _set_auth_cookie(raw_client, test_user)
    raw_client.cookies.set(CSRF_COOKIE_NAME, generate_csrf_token())

    response = raw_client.post(
        "/profile/language",
        data={"lang": "en", CSRF_FIELD_NAME: generate_csrf_token()},
    )

    assert response.status_code == 403


def test_post_with_forged_unsigned_token_is_rejected(raw_client, test_user):
    """Matching cookie and field are not enough; the token must be ours."""
    _set_auth_cookie(raw_client, test_user)
    forged = "attacker.value"
    raw_client.cookies.set(CSRF_COOKIE_NAME, forged)

    response = raw_client.post(
        "/profile/language",
        data={"lang": "en", CSRF_FIELD_NAME: forged},
    )

    assert response.status_code == 403


def test_login_post_without_token_is_rejected(raw_client):
    """Login is unauthenticated but still needs protection against login CSRF."""
    response = raw_client.post("/login", data={"username": "x", "password": "y"})

    assert response.status_code == 403


# ============ Middleware: accepting legitimate POSTs ============


def test_post_with_matching_token_is_accepted(raw_client, test_user):
    _set_auth_cookie(raw_client, test_user)
    token = generate_csrf_token()
    raw_client.cookies.set(CSRF_COOKIE_NAME, token)

    response = raw_client.post(
        "/profile/language",
        data={"lang": "en", CSRF_FIELD_NAME: token},
        follow_redirects=False,
    )

    assert response.status_code == 302


def test_request_body_survives_middleware(raw_client, test_user):
    """The middleware buffers and replays the body, so form data still arrives."""
    _set_auth_cookie(raw_client, test_user)
    token = generate_csrf_token()
    raw_client.cookies.set(CSRF_COOKIE_NAME, token)

    raw_client.post(
        "/profile/language",
        data={"lang": "en", CSRF_FIELD_NAME: token},
        follow_redirects=False,
    )

    raw_client.get("/profile")
    assert test_user.language == "en"


# ============ Middleware: exemptions ============


def test_api_routes_are_exempt(raw_client, test_user):
    """Header-authenticated API traffic cannot be driven by a browser form."""
    response = raw_client.get("/api/v1/health")

    assert response.status_code != 403


def test_bearer_authenticated_post_is_exempt(raw_client, test_user):
    """Authorization headers are never sent cross-site by a form submission."""
    response = raw_client.post(
        "/profile/language",
        data={"lang": "en"},
        headers={"Authorization": "Bearer some-api-key"},
    )

    assert response.status_code != 403


# ============ Rendered forms carry the token ============


def test_login_form_contains_csrf_field(raw_client):
    response = raw_client.get("/login")

    assert f'name="{CSRF_FIELD_NAME}"' in response.text


def test_authenticated_page_forms_contain_csrf_field(raw_client, test_user):
    _set_auth_cookie(raw_client, test_user)

    response = raw_client.get("/profile")

    assert f'name="{CSRF_FIELD_NAME}"' in response.text
