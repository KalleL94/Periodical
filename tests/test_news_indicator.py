"""Tests for the unread-release indicator (app/core/news.py)."""

from unittest.mock import patch

import pytest

from app.auth.auth import create_access_token
from app.core.news import get_latest_version, has_unseen_news, mark_seen
from app.database.database import User
from app.routes.changelog import VERSIONS

LATEST = VERSIONS[0]["version"]


def _set_auth_cookie(client, user) -> None:
    """Authenticate the test client as `user` (mirrors the app's cookie auth)."""
    client.cookies.set("access_token", f"Bearer {create_access_token(data={'sub': str(user.id)})}")


# ============ Version resolution ============


def test_latest_version_is_the_first_changelog_entry():
    assert get_latest_version() == LATEST


def test_empty_changelog_reports_no_news(test_user):
    """A changelog with no releases must not render an indicator pointing at nothing."""
    with patch("app.routes.changelog.VERSIONS", []):
        assert get_latest_version() is None
        assert has_unseen_news(test_user) is False


# ============ Seen / unseen ============


def test_user_who_never_opened_the_page_has_unseen_news(test_user):
    """Fresh and migrated rows are NULL, and must surface the notes once."""
    assert test_user.seen_release is None
    assert has_unseen_news(test_user) is True


def test_user_on_the_latest_release_has_no_unseen_news(test_user):
    test_user.seen_release = LATEST
    assert has_unseen_news(test_user) is False


def test_user_on_an_older_release_has_unseen_news(test_user):
    test_user.seen_release = "0.0.1"
    assert has_unseen_news(test_user) is True


def test_logged_out_visitor_has_no_unseen_news():
    assert has_unseen_news(None) is False


# ============ Acknowledgement ============


def test_mark_seen_persists_the_latest_version(test_db, test_user):
    mark_seen(test_db, test_user)
    assert test_db.query(User).filter(User.id == test_user.id).first().seen_release == LATEST


def test_mark_seen_is_a_no_op_without_a_user(test_db):
    mark_seen(test_db, None)  # must not raise


def test_mark_seen_on_empty_changelog_records_nothing(test_db, test_user):
    with patch("app.routes.changelog.VERSIONS", []):
        mark_seen(test_db, test_user)
    assert test_user.seen_release is None


# ============ End to end through the nav ============


@pytest.fixture
def logged_in_client(test_client, test_user):
    _set_auth_cookie(test_client, test_user)
    return test_client


# The app renders through two independent paths that each build their own
# template context: routes.shared.render() and core.helpers.render_template().
# base.html reads has_news on every page, so both must supply it. Testing only
# one of them is what let the indicator ship broken on every schedule view.
@pytest.mark.parametrize(
    ("path", "render_path"),
    [
        ("/profile", "routes.shared.render"),
        ("/", "core.helpers.render_template"),
    ],
)
def test_nav_shows_indicator_on_both_render_paths(logged_in_client, path, render_path):
    response = logged_in_client.get(path)
    assert response.status_code == 200, f"{path} did not render"
    assert "nav-link--news" in response.text, f"indicator missing on the {render_path} path"


def test_visiting_the_changelog_clears_the_indicator(logged_in_client, test_db, test_user):
    changelog = logged_in_client.get("/changelog")
    assert changelog.status_code == 200
    # The page never advertises itself
    assert "nav-link--news" not in changelog.text
    # The acknowledgement is on the user, not the browser
    test_db.refresh(test_user)
    assert test_user.seen_release == LATEST
    assert "nav-link--news" not in logged_in_client.get("/").text


def test_acknowledgement_follows_the_user_across_browsers(test_client, test_db, test_user):
    """Reading the notes on one device must clear them on every other device."""
    test_user.seen_release = LATEST
    test_db.commit()

    # A client with no prior cookies stands in for a second device
    _set_auth_cookie(test_client, test_user)
    assert "nav-link--news" not in test_client.get("/").text


def test_logged_out_visitors_never_see_the_indicator(test_client):
    assert "nav-link--news" not in test_client.get("/login").text


def test_changelog_is_readable_while_logged_out(test_client):
    """Acknowledgement must not be a precondition for reading the page."""
    assert test_client.get("/changelog").status_code == 200
