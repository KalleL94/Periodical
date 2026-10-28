"""The compare screen at /swaps/new renders for a logged-in user and lists colleagues."""

from app.auth.auth import create_access_token
from app.database.database import User, UserRole, WageType


def _add_user(db, uid, name, role=UserRole.USER, active=1):
    db.add(
        User(
            id=uid,
            username=f"u{uid}",
            password_hash="x",
            name=name,
            role=role,
            wage=30000,
            wage_type=WageType.MONTHLY,
            person_id=uid,
            vacation={},
            must_change_password=0,
            is_active=active,
        )
    )


def test_new_page_renders(test_client, test_db):
    _add_user(test_db, 1, "Me Myself")
    _add_user(test_db, 2, "Anna Colleague")
    _add_user(test_db, 3, "Boss Admin", role=UserRole.ADMIN)
    _add_user(test_db, 4, "Gone Person", active=0)
    test_db.commit()
    test_client.cookies.set("access_token", create_access_token(data={"sub": "1"}))

    resp = test_client.get("/swaps/new", params={"date": "2026-10-27", "with": "2"})
    assert resp.status_code == 200
    page = resp.text
    assert 'id="swap-compare"' in page
    assert "Anna Colleague" in page
    assert 'data-date="2026-10-27"' in page
    assert 'data-with="2"' in page
    # The window steps need the pair cap and the server's start horizon.
    assert 'data-max="40"' in page
    assert 'data-horizon="400"' in page
    assert 'name="csrf_token"' in page
    assert "/static/js/swap-compare.js" in page
    # Neither the viewer, admins nor inactive users are colleagues.
    assert "Boss Admin" not in page
    assert "Gone Person" not in page
    assert '<option value="1"' not in page


def test_new_page_requires_login(test_client):
    resp = test_client.get("/swaps/new", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]
