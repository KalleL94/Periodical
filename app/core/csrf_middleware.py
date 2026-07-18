# app/core/csrf_middleware.py
"""ASGI middleware enforcing CSRF tokens on state-changing requests (issue #156).

Deliberately a raw ASGI middleware rather than a BaseHTTPMiddleware subclass:
validating a form token means reading the request body, and BaseHTTPMiddleware
consumes the receive channel when it does so, leaving the route handler with an
empty body. Here the body is buffered once and replayed downstream through a
fresh receive callable, so handlers see the request unchanged.
"""

import os
from urllib.parse import parse_qs

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth.auth import ACCESS_TOKEN_EXPIRE_MINUTES
from app.auth.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_FIELD_NAME,
    generate_csrf_token,
    is_signed_token,
    tokens_match,
)

# Methods that must not change state, so they need no token (RFC 9110).
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# The /api/v1 sub-apps authenticate with an Authorization header and expose no
# state-changing routes; /static serves files. Neither is reachable by a
# cross-site form submission carrying our cookie.
EXEMPT_PATH_PREFIXES = ("/api/v1", "/static")

_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"


class CSRFMiddleware:
    """Reject unsafe requests whose double-submit token is missing or wrong."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        cookie_token = _cookie_token(headers)

        if scope["method"] in SAFE_METHODS:
            await self._handle_safe_request(scope, receive, send, cookie_token)
            return

        if self._is_exempt(scope, headers):
            await self.app(scope, receive, send)
            return

        body = await _read_body(receive)
        if body is None:  # client disconnected mid-request
            return

        if not tokens_match(cookie_token, _form_token(headers, body)):
            response = PlainTextResponse(
                "CSRF verification failed. Reload the page and try again.",
                status_code=403,
            )
            await response(scope, receive, send)
            return

        # Routes that re-render a form after a failed POST (login errors, for
        # example) need the token in their context too.
        _publish(scope, cookie_token)
        await self.app(scope, _replay(body), send)

    async def _handle_safe_request(self, scope: Scope, receive: Receive, send: Send, cookie_token: str | None) -> None:
        """Pass the request through, seeding a token cookie when one is needed.

        An already-valid token is kept rather than rotated, so forms rendered in
        other tabs stay submittable. The token is published on the request state
        so template rendering can embed the same value it just issued.
        """
        if is_signed_token(cookie_token):
            _publish(scope, cookie_token)
            await self.app(scope, receive, send)
            return

        token = generate_csrf_token()
        _publish(scope, token)

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append("set-cookie", _cookie_header(token))
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    def _is_exempt(self, scope: Scope, headers: Headers) -> bool:
        if scope["path"].startswith(EXEMPT_PATH_PREFIXES):
            return True
        # Browsers never attach an Authorization header to a cross-site form
        # POST, so header-authenticated clients are structurally CSRF-immune.
        return headers.get("authorization", "").startswith("Bearer ")


def _publish(scope: Scope, token: str | None) -> None:
    """Expose the active token on request.state for template rendering."""
    scope.setdefault("state", {})["csrf_token"] = token


def _cookie_token(headers: Headers) -> str | None:
    cookies = headers.get("cookie")
    if not cookies:
        return None
    for part in cookies.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name == CSRF_COOKIE_NAME:
            return value
    return None


def _form_token(headers: Headers, body: bytes) -> str | None:
    """Extract the token field from a urlencoded body.

    Every form in the app is urlencoded, so any other content type on an
    unsafe request is unexpected and fails closed.
    """
    if not headers.get("content-type", "").startswith(_FORM_CONTENT_TYPE):
        return None
    fields = parse_qs(body.decode("utf-8", errors="replace"))
    values = fields.get(CSRF_FIELD_NAME)
    return values[0] if values else None


async def _read_body(receive: Receive) -> bytes | None:
    """Drain the receive channel, returning None if the client disconnected."""
    body = b""
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return None
        body += message.get("body", b"")
        if not message.get("more_body", False):
            return body


def _replay(body: bytes) -> Receive:
    """Return a receive callable that re-delivers an already-consumed body."""
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


def _cookie_header(token: str) -> str:
    is_production = os.getenv("PRODUCTION", "false").lower() == "true"
    attributes = [
        f"{CSRF_COOKIE_NAME}={token}",
        "Path=/",
        f"Max-Age={ACCESS_TOKEN_EXPIRE_MINUTES * 60}",
        "SameSite=Lax",
        "HttpOnly",  # no client script reads the token; the server renders it
    ]
    if is_production:
        attributes.append("Secure")
    return "; ".join(attributes)
