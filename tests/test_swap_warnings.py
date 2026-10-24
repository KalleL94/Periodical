"""Tests for app.core.swaps.proposal_warnings (rest rules as warnings)."""

import datetime

import pytest

from app.core import swaps
from app.core.schedule import clear_schedule_cache
from app.core.swaps import proposal_warnings
from app.database.database import ShiftOverride, User, UserRole, WageType

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
        # ISO week 44 keeps 46 h (Mon 00:00 to Tue 22:00); only a rolling window fell short.
        ["Under 11 timmars dygnsvila (du)"],
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
    # User 9 keeps 56 h in ISO week 44 (Thu 22:00 to Sun 06:00); only a rolling window fell short.
    ("iso_week_ok_target", 7, 9, D(2026, 10, 27), D(2026, 10, 30), []),
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
def test_single_pair_warnings(db, req_id, tgt_id, req_date, tgt_date, expected):
    assert _warn(db, req_id, tgt_id, [(req_date, tgt_date)]) == [expected]


def test_proposal_warnings_logs_and_returns_empty_on_error(db, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("broken helper")

    monkeypatch.setattr(swaps, "_day", boom)
    with caplog.at_level("ERROR", logger="app.core.swaps"):
        assert _warn(db, 7, 8, [(D(2026, 10, 25), D(2026, 11, 1))] * 2) == [[], []]
    assert "broken helper" in caplog.text


DAILY_DU = "Under 11 timmars dygnsvila (du)"
WEEKLY_DU = "Under 36 timmars veckovila (du)"


def _warn(db, req_id, tgt_id, pairs):
    return proposal_warnings(db, db.get(User, req_id), db.get(User, tgt_id), pairs)


def _override(db, user_id, day, code, start=None, end=None):
    db.add(ShiftOverride(user_id=user_id, date=day, shift_code=code, start_time=start, end_time=end, created_by=1))
    db.commit()
    clear_schedule_cache()


def test_adjacent_pairs_break_daily_rest_together(db):
    # User 7 has N2 on Mon 26 and (override) N1 on Tue 27. User 10 is free both days.
    # Taking over either shift alone leaves 11 h; taking both gives N2 22:00 then N1 06:00.
    _override(db, 7, D(2026, 10, 27), "N1")
    mon, tue = (D(2026, 10, 26),) * 2, (D(2026, 10, 27),) * 2
    assert DAILY_DU not in _warn(db, 10, 7, [mon])[0]
    assert DAILY_DU not in _warn(db, 10, 7, [tue])[0]
    both = _warn(db, 10, 7, [mon, tue])
    assert all(DAILY_DU in w for w in both)


def test_week_of_pairs_breaks_iso_weekly_rest(db):
    # User 7 is free Mon 2 to Wed 4 Nov and starts N3 on Thu 5. Taking over user 9's
    # N1 on all three days leaves 32 h (Wed 14:00 to Thu 22:00), each one alone is fine.
    days = [(D(2026, 11, d), D(2026, 11, d)) for d in (2, 3, 4)]
    for pair in days:
        assert _warn(db, 7, 9, [pair]) == [[]]
    assert _warn(db, 7, 9, days) == [[WEEKLY_DU]] * 3


def test_week_already_short_and_not_worse_gives_no_warning(db):
    # User 8's week 44 is already short with N2 on Thu 29 (longest rest 26 h, Thu 22:00 to Sat 00:00).
    # Swapping N3 for user 7's N2 on Mon 26 leaves that longest rest as it was.
    _override(db, 8, D(2026, 10, 29), "N2")
    [warnings] = _warn(db, 8, 7, [(D(2026, 10, 26), D(2026, 10, 26))])
    assert WEEKLY_DU not in warnings


def test_sunday_to_monday_crossing_uses_iso_weeks(db):
    # User 7 works Sat 24 to Thu 29: a rolling window from Sat 24 has under 36 h rest, but
    # ISO week 43 and week 44 each keep a long rest. Taking user 9's N2 on Fri 30 keeps that.
    assert _warn(db, 7, 9, [(D(2026, 11, 8), D(2026, 10, 30))]) == [[]]


def test_custom_times_ending_after_midnight_count(db):
    # A manual override keeps user 7 on N2 until 04:00 on Fri 30; user 9's N2 that day starts 14:00.
    _override(db, 7, D(2026, 10, 29), "N2", datetime.time(14, 0), datetime.time(4, 0))
    assert _warn(db, 7, 9, [(D(2026, 10, 30), D(2026, 10, 30))]) == [[DAILY_DU]]


def test_on_call_without_clock_times_still_blocks_weekly_rest(db, monkeypatch):
    # The dev schedule returns OC days without start and end. User 11 has OC on Wed 4 Nov;
    # taking user 12's N1 on Fri 6 leaves 30 h (Thu 00:00 to Fri 06:00) only if OC counts.
    real = swaps.build_week_data

    def oc_without_times(*args, **kwargs):
        days = real(*args, **kwargs)
        for d in days:
            if d.get("shift") and d["shift"].code == "OC":
                d["start"] = d["end"] = None
        return days

    monkeypatch.setattr(swaps, "build_week_data", oc_without_times)
    assert _warn(db, 11, 12, [(D(2026, 11, 6), D(2026, 11, 6))]) == [[WEEKLY_DU]]


def test_existing_short_gap_does_not_hide_a_new_one_on_the_other_date(db):
    # User 7 already has 6.5 h rest from Mon 26 N2 (22:30) to an early N1 on Tue 27 (05:00).
    # Giving Tue 27 away and taking user 10's N1 on Fri 30 adds 7.5 h after Thu 29 N2.
    # The new gap is longer than the old one, but it is still a new breach on Fri 30.
    _override(db, 7, D(2026, 10, 27), "N1", datetime.time(5, 0), datetime.time(14, 30))
    [warnings] = _warn(db, 7, 10, [(D(2026, 10, 27), D(2026, 10, 30))])
    assert DAILY_DU in warnings
