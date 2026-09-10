"""Response security headers, robots.txt and the favicon link.

The middleware is registered outermost, so the headers must reach responses the
inner middleware never lets through to a route (the CSRF 403) and the files
served from the static mount, not just ordinary route responses.
"""

import os

import pytest

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


@pytest.mark.parametrize("prefix", ["", "/api/v1", "/api/v1/admin"])
@pytest.mark.parametrize("page", ["/docs", "/redoc", "/openapi.json"])
def test_every_mounted_docs_page_is_gated_and_gets_the_cdn(test_client, prefix, page):
    """The API sub-apps mount their own docs, and each mount needs both halves.

    DOC_PATHS drives the relaxed CSP and the docs gate alike, so a mount left
    out of it renders blank (Swagger's bundle blocked) and renders for anyone.
    The user API's three pages were missing exactly that way.
    """
    resp = test_client.get(f"{prefix}{page}", follow_redirects=False)

    assert resp.status_code in (302, 307), "anonymous callers must be sent to /login"
    assert resp.headers["location"] == "/login"
    assert "https://cdn.jsdelivr.net" in resp.headers["content-security-policy"]


def _signed_in(test_client, test_db, monkeypatch, role):
    """A client carrying a session cookie for a user of the given role.

    SessionLocal is rebound because protect_docs opens its own session rather
    than taking the request's dependency, so without this it reads the real
    database and finds no such user.
    """
    from sqlalchemy.orm import sessionmaker

    import app.database.database as db_module
    from app.auth.auth import create_access_token
    from app.database.database import User, UserRole, WageType

    engine = test_db.get_bind()
    monkeypatch.setattr(db_module, "SessionLocal", sessionmaker(autocommit=False, autoflush=False, bind=engine))
    test_db.add(
        User(
            id=1,
            username="u1",
            password_hash="x",
            name="Someone",
            role=UserRole.ADMIN if role == "admin" else UserRole.USER,
            wage=30000,
            wage_type=WageType.MONTHLY,
            vacation={},
            must_change_password=0,
            is_active=1,
            person_id=1,
        )
    )
    test_db.commit()
    test_client.cookies.set("access_token", f"Bearer {create_access_token(data={'sub': '1'})}")
    return test_client


@pytest.mark.parametrize("page", ["/docs", "/redoc", "/openapi.json"])
def test_a_signed_in_user_reads_the_user_api_docs(test_client, test_db, monkeypatch, page):
    """The user API's docs describe what a user's own API key already reaches."""
    client = _signed_in(test_client, test_db, monkeypatch, role="user")

    assert client.get(f"/api/v1{page}", follow_redirects=False).status_code == 200


@pytest.mark.parametrize("path", ["/docs", "/api/v1/admin/docs", "/api/v1/admin/openapi.json"])
def test_a_signed_in_user_is_kept_out_of_the_admin_docs(test_client, test_db, monkeypatch, path):
    """The root app's schema and the admin API stay admin-only."""
    client = _signed_in(test_client, test_db, monkeypatch, role="user")
    resp = client.get(path, follow_redirects=False)

    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/login"


@pytest.mark.parametrize("prefix", ["", "/api/v1", "/api/v1/admin"])
def test_an_admin_reads_every_docs_page(test_client, test_db, monkeypatch, prefix):
    client = _signed_in(test_client, test_db, monkeypatch, role="admin")

    assert client.get(f"{prefix}/docs", follow_redirects=False).status_code == 200
