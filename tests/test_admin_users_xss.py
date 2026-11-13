"""A user name must never land inside an inline JS string."""

import re

from tests.test_admin_reset_password import _login

PAYLOAD = "X'+alert(1)+'"


def test_user_name_is_not_inside_inline_handler(test_client, test_db, admin_user, test_user):
    test_user.name = PAYLOAD
    test_db.commit()
    _login(test_client, "admin", "adminpass123")

    html = test_client.get("/admin/users").text

    handlers = re.findall(r'\son\w+="([^"]*)"', html)
    assert handlers
    assert not any("alert" in h for h in handlers)
