"""Tests for shared chrome in base.html."""

import pytest


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
