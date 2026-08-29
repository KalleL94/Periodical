"""Response security headers, robots.txt and the favicon link.

The middleware is registered outermost, so the headers must reach responses the
inner middleware never lets through to a route (the CSRF 403) and the files
served from the static mount, not just ordinary route responses.
"""

import os

from app.core.security_headers import HSTS, SecurityHeadersMiddleware


def test_headers_present_on_a_route_response(test_client):
    headers = test_client.get("/health").headers

    assert headers["x-frame-options"] == "DENY"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "object-src 'none'" in headers["content-security-policy"]


def test_headers_present_on_static_files(test_client):
    # The static mount is inside the middleware stack, so it is covered too.
    resp = test_client.get("/static/favicon.svg")

    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_headers_present_on_a_csrf_rejection(raw_client):
    # CSRFMiddleware answers this itself without reaching a route handler.
    # raw_client skips the token injection, so the rejection actually happens.
    resp = raw_client.post("/login", data={"username": "x", "password": "y"})

    assert resp.status_code == 403
    assert resp.headers["x-frame-options"] == "DENY"


def test_hsts_only_in_production(monkeypatch):
    monkeypatch.setenv("PRODUCTION", "false")
    assert "strict-transport-security" not in SecurityHeadersMiddleware(app=None).headers

    monkeypatch.setenv("PRODUCTION", "true")
    assert SecurityHeadersMiddleware(app=None).headers["strict-transport-security"] == HSTS


def test_robots_disallows_everything(test_client):
    resp = test_client.get("/robots.txt")

    assert resp.status_code == 200
    assert resp.text == "User-agent: *\nDisallow: /\n"


def test_favicon_is_linked_and_exists(test_client):
    # A link tag pointing at a missing file leaves the default browser icon,
    # which is the state this change exists to fix.
    assert '<link rel="icon" href="/static/favicon.svg' in test_client.get("/login").text
    assert os.path.exists("app/static/favicon.svg")


def test_docs_csp_allows_the_swagger_cdn(test_client):
    # Swagger UI loads its bundle from jsDelivr; a strict script-src would leave
    # the admin-only docs page blank. The redirect for a non-admin carries the
    # same relaxed policy, which is what this asserts without needing a login.
    csp = test_client.get("/docs", follow_redirects=False).headers["content-security-policy"]

    assert "https://cdn.jsdelivr.net" in csp
    assert "frame-ancestors 'none'" in csp


def test_ordinary_pages_do_not_allow_the_cdn(test_client):
    assert "cdn.jsdelivr.net" not in test_client.get("/login").headers["content-security-policy"]
