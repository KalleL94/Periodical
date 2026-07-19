"""Tests for shared chrome in base.html and the single render path behind it."""

import pytest

from app.auth.auth import create_access_token
from app.core.utils import get_today


def _set_auth_cookie(client, user) -> None:
    """Authenticate the test client as `user` (mirrors the app's cookie auth)."""
    client.cookies.set("access_token", f"Bearer {create_access_token(data={'sub': str(user.id)})}")


@pytest.mark.parametrize("path", ["/login", "/changelog"])
def test_wordmark_links_home(test_client, path):
    """The header wordmark is the way back to the dashboard from anywhere.

    Asserted rather than eyeballed because a template change that drops the
    anchor produces no error: the heading still renders, it just stops being
    clickable.
    """
    response = test_client.get(path)
    assert response.status_code == 200
    assert '<a href="/" class="app-title-link">Periodical</a>' in response.text


@pytest.mark.parametrize("path", ["/", "/profile"])
def test_my_day_link_points_at_today(test_client, test_user, path):
    """`now` must be resolved per request, not once when the module is imported.

    It used to be a Jinja global assigned at import time, so the "My day" link
    froze on whatever day the process started and drifted further from today the
    longer the server ran. Both pages are checked because they were served by
    two different render paths before those were merged.
    """
    _set_auth_cookie(test_client, test_user)
    today = get_today()

    response = test_client.get(path)
    assert response.status_code == 200
    assert f"/day/{test_user.id}/{today.year}/{today.month}/{today.day}" in response.text


def test_there_is_only_one_render_path():
    """Shared template context is assembled in exactly one place.

    Two render paths each building their own context for the same base.html is
    what let a nav entry go missing on half the app without any error: Jinja
    treats an undefined name as falsy and says nothing. If a second path is
    reintroduced, every variable base.html reads has to be kept in sync by hand
    again.
    """
    import app.core.helpers as helpers

    assert not hasattr(helpers, "render_template"), (
        "core.helpers.render_template is back; shared context belongs in routes.shared.render()"
    )
