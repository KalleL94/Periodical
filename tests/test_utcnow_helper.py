"""Test for the utcnow() helper (audit item D1).

The helper replaces the deprecated datetime.utcnow(). It must keep returning a NAIVE UTC
datetime, since timestamps and DateTime column defaults across the app assume naive UTC and
mixing naive/aware values would break comparisons.
"""

from datetime import UTC, datetime

from app.database.database import utcnow


def test_utcnow_is_naive():
    now = utcnow()
    assert now.tzinfo is None


def test_utcnow_matches_current_utc():
    before = datetime.now(UTC).replace(tzinfo=None)
    value = utcnow()
    after = datetime.now(UTC).replace(tzinfo=None)
    assert before <= value <= after
