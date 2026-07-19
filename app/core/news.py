# app/core/news.py
"""
Unseen-release tracking for the changelog page.

The changelog holds user-facing release notes that nobody reads, because the
only way in is a footer link. This module answers one question for every
rendered page: has this user seen the current release notes yet? The answer
drives a nav entry that appears only when there is something new.

"Seen" is stored on the User row (``users.seen_release``), so the acknowledgement
follows the person rather than the browser: reading the notes on your phone also
clears them on your desktop. NULL means the user has never opened the page, which
counts as unseen -- on the first deploy every user is pointed at the notes once,
which is the entire reason the feature exists.
"""

from sqlalchemy.orm import Session

from app.database.database import User


def get_latest_version() -> str | None:
    """Return the newest version in the changelog, or None if it is empty.

    Imported lazily: changelog.py imports render() from routes.shared, which is
    what calls into this module, so a module-level import would be circular.
    """
    from app.routes.changelog import VERSIONS

    if not VERSIONS:
        return None
    return VERSIONS[0]["version"]


def has_unseen_news(user: User | None) -> bool:
    """True when the newest release differs from the one this user acknowledged.

    Logged-out visitors get False: the release notes describe the app you are
    signed in to, and there is nobody to remember the acknowledgement for.
    """
    if user is None:
        return False
    latest = get_latest_version()
    if latest is None:
        return False
    return getattr(user, "seen_release", None) != latest


def mark_seen(session: Session, user: User | None) -> None:
    """Record the newest release as acknowledged by this user.

    A no-op when there is nothing to record, so the changelog page stays
    readable while logged out and on an empty changelog.
    """
    if user is None:
        return
    latest = get_latest_version()
    if latest is None or user.seen_release == latest:
        return

    user.seen_release = latest
    session.commit()
