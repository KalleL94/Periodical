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

import json
from functools import cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.database.database import User

RELEASES_PATH = Path("data/releases.json")


@cache
def load_releases() -> list[dict]:
    """Return the release notes, newest first.

    They used to be a 1200-line ``VERSIONS`` literal inside
    ``app/routes/changelog.py``, which made a route module the place both this
    module and ``main.py`` reached into for the application's version number,
    and forced the import here to be function-local to break the resulting
    cycle. As data in ``data/`` they are loaded like every other data file, and
    ``main.py`` validates the JSON at startup alongside the rest.

    Cached: the file is read-only at runtime, edited by a release and shipped.
    """
    return json.loads(RELEASES_PATH.read_text(encoding="utf-8"))


def get_latest_version() -> str | None:
    """Return the newest version in the changelog, or None if it is empty."""
    releases = load_releases()
    if not releases:
        return None
    return releases[0]["version"]


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
