"""The admin gate lives on the routers in admin.py, admin_users.py and substitutes.py.

These routes no longer declare current_user themselves, so this asserts the
router-level dependency still rejects a non-admin with 403 on every one of the
three routers.
"""

import pytest


def _login(client, username, password):
    client.post("/login", data={"username": username, "password": password})


@pytest.mark.parametrize(
    "method,path,data",
    [
        # admin.py router (prefix /admin), handler no longer takes current_user
        ("post", "/admin/vacation/1/weeks", {"year": "2026", "weeks": "1,2"}),
        # admin_users.py router (no prefix, all paths under /admin/)
        ("post", "/admin/users/1/delete-wage/1", {}),
        # substitutes.py router (no prefix, all paths under /admin/substitutes)
        ("post", "/admin/substitutes/1/toggle", {}),
    ],
)
def test_non_admin_is_forbidden(test_client, test_user, method, path, data):
    _login(test_client, "testuser", "testpass123")
    resp = getattr(test_client, method)(path, data=data, follow_redirects=False)
    assert resp.status_code == 403


def test_admin_reaches_the_handler(test_client, admin_user):
    """Sanity check: the gate lets an admin through (404 comes from the handler, not the gate)."""
    _login(test_client, "admin", "adminpass123")
    resp = test_client.post("/admin/substitutes/999/toggle", data={}, follow_redirects=False)
    assert resp.status_code == 404
