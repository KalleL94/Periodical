# app/auth/csrf.py
"""CSRF protection using the signed double-submit cookie pattern (issue #156).

The app has no server-side session store, which rules out the synchroniser-token
pattern. Instead every browser session gets a random token that is sent twice:
once in a cookie and once as a hidden form field. A cross-site attacker can make
the browser send our cookie, but cannot read it to populate the form field, so
the two values only agree on requests that genuinely originated from our pages.

The token is HMAC-signed with SECRET_KEY so a value the server never issued is
rejected outright. That closes the double-submit pattern's known weakness: an
attacker controlling a sibling subdomain can write cookies for the parent
domain, and without a signature they could plant a value they also know.
"""

import hashlib
import hmac
import secrets

from app.auth.auth import SECRET_KEY

CSRF_COOKIE_NAME = "csrf_token"
CSRF_FIELD_NAME = "csrf_token"

# 32 bytes of entropy, matching the strength of the auth tokens elsewhere.
_NONCE_BYTES = 32


def _sign(nonce: str) -> str:
    """Return the HMAC-SHA256 signature binding a nonce to this deployment."""
    return hmac.new(SECRET_KEY.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_csrf_token() -> str:
    """Return a fresh signed token of the form "<nonce>.<signature>"."""
    nonce = secrets.token_urlsafe(_NONCE_BYTES)
    return f"{nonce}.{_sign(nonce)}"


def is_signed_token(token: str | None) -> bool:
    """Return True when `token` carries a signature this server issued."""
    if not token:
        return False
    nonce, separator, signature = token.partition(".")
    if not separator or not nonce or not signature:
        return False
    return hmac.compare_digest(signature, _sign(nonce))


def get_csrf_token(request) -> str:
    """Return the token CSRFMiddleware published for this request.

    Falls back to an empty string when there is no request (or no middleware,
    as in unit tests that call render() directly), so templates always render.
    """
    if request is None:
        return ""
    return getattr(request.state, "csrf_token", "") or ""


def tokens_match(cookie_token: str | None, form_token: str | None) -> bool:
    """Return True when the double-submit pair is present, signed and equal."""
    if not cookie_token or not form_token:
        return False
    if not is_signed_token(cookie_token):
        return False
    return hmac.compare_digest(cookie_token, form_token)
