"""Tests for app.core.swaps.swap_warnings (rest rules as warnings)."""

import datetime

import pytest

from app.core import swaps
from app.core.swaps import swap_warnings
from app.database.database import User, UserRole, WageType

D = datetime.date


@pytest.fixture
def db(rotation_session, monkeypatch):
    monkeypatch.setattr(swaps, "get_today", lambda: D(2026, 10, 20))
    for uid, pos in ((7, 7), (8, 3), (9, 1), (10, 10), (11, 5), (12, 9)):
        rotation_session.add(
            User(
                id=uid,
                username=f"u{uid}",
                password_hash="x",
                name=f"User {uid}",
                role=UserRole.USER,
                wage=30000,
                wage_type=WageType.MONTHLY,
                person_id=pos,
                vacation={},
                must_change_password=0,
            )
        )
    rotation_session.commit()
    return rotation_session


CASES = [
    # Every breach is listed, so one pair can carry several warnings.
    # id, requester, target, req_date, tgt_date, expected warnings
    (
        "rest_11h_requester",
        8,
        10,
        D(2026, 10, 26),
        D(2026, 10, 29),
        ["Under 11 timmars dygnsvila (du)", "Under 36 timmars veckovila (du)"],
    ),
    (
        "rest_11h_target",
        7,
        8,
        D(2026, 10, 29),
        D(2026, 11, 1),
        ["Under 11 timmars dygnsvila (kollegan)", "Under 36 timmars veckovila (kollegan)"],
    ),
    (
        "rest_36h_requester",
        8,
        10,
        D(2026, 10, 26),
        D(2026, 10, 24),
        ["Under 36 timmars veckovila (du)", "Under 36 timmars veckovila (kollegan)"],
    ),
    ("rest_36h_target", 7, 9, D(2026, 10, 27), D(2026, 10, 30), ["Under 36 timmars veckovila (kollegan)"]),
    ("clean", 7, 8, D(2026, 10, 25), D(2026, 11, 1), []),
    ("same_day_clean", 8, 7, D(2026, 10, 25), D(2026, 10, 25), []),
    (
        "same_day_mutual_breach",
        8,
        7,
        D(2026, 10, 27),
        D(2026, 10, 27),
        ["Under 11 timmars dygnsvila (du)", "Under 11 timmars dygnsvila (kollegan)"],
    ),
    ("same_day_breach", 7, 10, D(2026, 10, 27), D(2026, 10, 27), ["Under 11 timmars dygnsvila (kollegan)"]),
]


@pytest.mark.parametrize(
    ("req_id", "tgt_id", "req_date", "tgt_date", "expected"),
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_swap_warnings(db, req_id, tgt_id, req_date, tgt_date, expected):
    assert swap_warnings(db, db.get(User, req_id), db.get(User, tgt_id), req_date, tgt_date) == expected


def test_swap_warnings_logs_and_returns_empty_on_error(db, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("broken helper")

    monkeypatch.setattr(swaps, "check_rest_ok", boom)
    with caplog.at_level("ERROR", logger="app.core.swaps"):
        assert swap_warnings(db, db.get(User, 7), db.get(User, 8), D(2026, 10, 25), D(2026, 11, 1)) == []
    assert "broken helper" in caplog.text
