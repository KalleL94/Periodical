"""Posting several dates to one edit route writes one row per date.

The conflict rule is deliberately asymmetric: one date upserts, several skip.
"""

import datetime

import pytest

from app.core.helpers import apply_to_dates, is_safe_redirect
from app.database.database import OvertimeShift
from app.routes.overtime import add_overtime_shift

D = datetime.date


def test_is_safe_redirect_rejects_absolute_and_protocol_relative():
    assert is_safe_redirect("/day/6/2026/9/17") is True
    assert is_safe_redirect("https://evil.example/x") is False
    assert is_safe_redirect("//evil.example/x") is False
    assert is_safe_redirect("") is False


def test_one_date_never_consults_the_conflict_check():
    """A single-date post upserts, which is what the day page relies on."""
    seen = []
    done = []

    def conflicts(date):
        seen.append(date)
        return "should not be asked"

    written, skipped = apply_to_dates([D(2026, 6, 1)], done.append, conflicts)
    assert seen == []
    assert skipped == []
    assert written == [D(2026, 6, 1)]
    assert done == [D(2026, 6, 1)]


def test_several_dates_skip_the_conflicting_one_and_write_the_rest():
    done = []
    dates = [D(2026, 6, 1), D(2026, 6, 2), D(2026, 6, 3)]

    def conflicts(date):
        return "hade redan VAB" if date == D(2026, 6, 2) else None

    written, skipped = apply_to_dates(dates, done.append, conflicts)
    assert written == [D(2026, 6, 1), D(2026, 6, 3)]
    assert skipped == [(D(2026, 6, 2), "hade redan VAB")]
    assert done == [D(2026, 6, 1), D(2026, 6, 3)]


@pytest.mark.anyio
async def test_posting_four_dates_creates_four_overtime_rows(test_db, test_user):
    dates = [D(2026, 6, 1), D(2026, 6, 2), D(2026, 6, 3), D(2026, 6, 4)]
    await add_overtime_shift(
        user_id=test_user.id,
        dates=dates,
        start_time=datetime.time(5, 0),
        end_time=datetime.time(6, 0),
        hours=1.0,
        kind="extra",
        side="before",
        return_to="/week/1?year=2026&week=23",
        session=test_db,
        current_user=test_user,
    )
    rows = test_db.query(OvertimeShift).all()
    assert {r.date for r in rows} == set(dates)


@pytest.mark.anyio
async def test_return_to_is_honoured_when_relative(test_db, test_user):
    response = await add_overtime_shift(
        user_id=test_user.id,
        dates=[D(2026, 6, 1)],
        start_time=datetime.time(5, 0),
        end_time=datetime.time(6, 0),
        hours=1.0,
        kind="extra",
        side="before",
        return_to="/week/1?year=2026&week=23",
        session=test_db,
        current_user=test_user,
    )
    assert response.headers["location"].startswith("/week/1")


@pytest.mark.anyio
async def test_an_absolute_return_to_falls_back_to_the_day_page(test_db, test_user):
    response = await add_overtime_shift(
        user_id=test_user.id,
        dates=[D(2026, 6, 1)],
        start_time=datetime.time(5, 0),
        end_time=datetime.time(6, 0),
        hours=1.0,
        kind="extra",
        side="before",
        return_to="https://evil.example/steal",
        session=test_db,
        current_user=test_user,
    )
    assert response.headers["location"] == f"/day/{test_user.id}/2026/6/1"


@pytest.mark.anyio
async def test_a_conflicting_date_is_skipped_and_named(test_db, test_user):
    """The same (kind, side) already on a date is left alone, the others written."""
    test_db.add(
        OvertimeShift(
            user_id=test_user.id,
            date=D(2026, 6, 2),
            start_time=datetime.time(9, 0),
            end_time=datetime.time(10, 0),
            hours=1.0,
            ot_pay=0.0,
            kind="extra",
            side="before",
        )
    )
    test_db.commit()

    response = await add_overtime_shift(
        user_id=test_user.id,
        dates=[D(2026, 6, 1), D(2026, 6, 2), D(2026, 6, 3)],
        start_time=datetime.time(5, 0),
        end_time=datetime.time(6, 0),
        hours=1.0,
        kind="extra",
        side="before",
        return_to="/week/1?year=2026&week=23",
        session=test_db,
        current_user=test_user,
    )

    untouched = test_db.query(OvertimeShift).filter(OvertimeShift.date == D(2026, 6, 2)).one()
    assert untouched.start_time == datetime.time(9, 0)
    assert test_db.query(OvertimeShift).count() == 3
    assert "result=" in response.headers["location"]
