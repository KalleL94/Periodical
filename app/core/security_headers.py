# app/core/security_headers.py
"""ASGI middleware attaching response security headers.

Raw ASGI rather than BaseHTTPMiddleware: the headers only need appending to
the response start message, which costs one dict lookup per request and does
not touch the body at all.

Registered last in main.py so it wraps the whole stack, which means the
headers also cover responses the inner middleware short-circuits (a CSRF 403,
for example) and the files served from /static.
"""

import os

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# ponytail: 'unsafe-inline' for script and style is a real weakening of the CSP.
# The templates carry inline <script> blocks in 22 files and onclick handlers in
# 21, so a nonce-based policy means moving all of that into static JS first. The
# directives below still block third-party script origins, framing, plugin
# content and form posts to other sites, which is what an XSS in this app would
# most plausibly reach for. Upgrade path: move handlers to addEventListener in
# app/static/js, then swap to 'nonce-<per-request>' and drop 'unsafe-inline'.
CSP = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self'",  # webfonts are self-hosted, see static/css/fonts.css
        "connect-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "object-src 'none'",
    )
)

# Swagger UI and ReDoc pull their bundle from jsDelivr, so the strict policy
# would leave the API docs blank. These paths get the CDN added instead of the
# whole app getting a looser default. Kept here (rather than in main.py) so the
# path list and the policy that depends on it stay together; main.py imports
# both sets below for the docs gate.
#
# One entry per mounted app: the root app plus both API sub-apps. Built as a
# product rather than listed, because leaving a mount out costs twice over: the
# page renders blank AND, since main.py reads the same set, it goes ungated.
_DOC_PAGES = ("/docs", "/redoc", "/openapi.json")
DOC_PATHS = frozenset(f"{prefix}{page}" for prefix in ("", "/api/v1", "/api/v1/admin") for page in _DOC_PAGES)

# The user API is what a user's own API key reaches, so its docs are for every
# signed-in user to read. The rest of DOC_PATHS is admin-only: the root app's
# own schema and the admin API, which needs an admin key to call at all.
USER_DOC_PATHS = frozenset(f"/api/v1{page}" for page in _DOC_PAGES)

_CDN = "https://cdn.jsdelivr.net"
DOCS_CSP = (
    CSP.replace("script-src 'self'", f"script-src 'self' {_CDN}")
    .replace("style-src 'self'", f"style-src 'self' {_CDN}")
    .replace("img-src 'self' data:", f"img-src 'self' data: {_CDN}")
    .replace("font-src 'self'", f"font-src 'self' {_CDN}")
    # ReDoc builds its renderer in a worker created from a blob.
    + "; worker-src 'self' blob:"
)

# Two years, the value the HSTS preload list expects. Sent only in production:
# development runs plain HTTP on localhost, and pinning HTTPS there would break
# every other local service sharing the hostname.
HSTS = "max-age=63072000; includeSubDomains"

BASE_HEADERS = {
    "content-security-policy": CSP,
    # frame-ancestors already covers this for current browsers; kept for the
    # ones that honour only the older header.
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    # Schedule URLs carry a person id, so outbound links leak the origin only.
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "geolocation=(), camera=(), microphone=(), payment=()",
}


class SecurityHeadersMiddleware:
    """Append the security headers to every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.headers = dict(BASE_HEADERS)
        if os.getenv("PRODUCTION", "false").lower() == "true":
            self.headers["strict-transport-security"] = HSTS

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        active = self.headers
        if scope["path"] in DOC_PATHS:
            active = {**self.headers, "content-security-policy": DOCS_CSP}

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in active.items():
                    # setdefault, so a route that needs its own policy keeps it.
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)
