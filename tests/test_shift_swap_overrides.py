"""Accepted swaps are written as ShiftOverride rows; cancel removes them.

Uses the rotation_session fixture (10-week test pattern). Week of Mon 26 Oct 2026:

    pos 3:  OFF N3  N3  N3  OFF OFF OC    (user 8)
    pos 7:  N2  N2  N2  N2  N2  OFF OFF   (user 7)
    pos 9:  N3  OFF OC  N3  N3  N3  N3    (user 12)
    pos 10: OFF OFF OFF N1  N1  N1  N1    (user 10)
"""

import datetime

import pytest
from fastapi import HTTPException

from app.core import swaps
from app.core.schedule import build_week_data, clear_schedule_cache
from app.database.database import ShiftOverride, ShiftSwap, SwapStatus, User, UserRole, WageType
from app.routes.shift_override import upsert_shift_override
from app.routes.shift_swap import accept_swap, cancel_swap, propose_swap

D = datetime.date
OCT28, OCT29, OCT27 = D(2026, 10, 28), D(2026, 10, 29), D(2026, 10, 27)


@pytest.fixture
def db(rotation_session, monkeypatch):
    monkeypatch.setattr(swaps, "get_today", lambda: D(2026, 10, 20))
    for uid, pos in ((7, 7), (8, 3), (10, 10), (12, 9)):
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


async def _propose(db, req_id, tgt_id, rd, td) -> ShiftSwap:
    await propose_swap(
        target_id=tgt_id,
        requester_date=rd,
        target_date=td,
        message=None,
        current_user=db.get(User, req_id),
        db=db,
    )
    return db.query(ShiftSwap).order_by(ShiftSwap.id.desc()).first()


async def _accepted(db, req_id, tgt_id, rd, td) -> ShiftSwap:
    swap = await _propose(db, req_id, tgt_id, rd, td)
    await accept_swap(swap.id, current_user=db.get(User, tgt_id), db=db)
    return swap


def _rows(db):
    return {(r.user_id, r.date): (r.shift_code, r.swap_id) for r in db.query(ShiftOverride).all()}


def _week(db, user_ids):
    """(user, date) -> (shift code, hours) for ISO week 44 as the schedule shows it."""
    clear_schedule_cache()
    out = {}
    for uid in user_ids:
        pos = db.get(User, uid).rotation_person_id
        for day in build_week_data(2026, 44, person_id=pos, session=db):
            shift = day.get("shift")
            out[(uid, day["date"])] = (shift.code if shift else None, day.get("hours"))
    return out


@pytest.mark.anyio
async def test_propose_stores_flag(db):
    swap = await _propose(db, 12, 7, OCT28, OCT29)
    assert swap.applied_as_override is True
    assert swap.status == SwapStatus.PENDING
    assert (swap.requester_shift_code, swap.target_shift_code) == ("N3", "N2")


@pytest.mark.anyio
async def test_propose_refuses_with_check_swap_reason(db):
    with pytest.raises(HTTPException) as exc:
        await _propose(db, 8, 7, OCT29, D(2026, 10, 26))
    assert exc.value.status_code == 400
    assert exc.value.detail == "Du jobbar inte det datumet"
    assert db.query(ShiftSwap).count() == 0


@pytest.mark.anyio
async def test_accept_writes_override_rows(db):
    swap = await _accepted(db, 12, 7, OCT28, OCT29)
    db.refresh(swap)
    assert swap.status == SwapStatus.ACCEPTED
    # Both work both days, so the two people exchange shifts on each date.
    assert _rows(db) == {
        (12, OCT28): ("N2", swap.id),
        (7, OCT28): ("N3", swap.id),
        (7, OCT29): ("N3", swap.id),
        (12, OCT29): ("N2", swap.id),
    }
    assert {r.created_by for r in db.query(ShiftOverride).all()} == {7}


@pytest.mark.anyio
async def test_accept_one_way_writes_two_rows(db):
    swap = await _accepted(db, 8, 10, OCT27, OCT27)
    assert _rows(db) == {(8, OCT27): ("OFF", swap.id), (10, OCT27): ("N3", swap.id)}


@pytest.mark.anyio
async def test_accept_refuses_stale_codes(db):
    swap = await _propose(db, 12, 7, OCT28, OCT29)
    # Simulate the rotation changing after the proposal: the stored code no longer matches.
    swap.requester_shift_code = "N1"
    db.commit()
    with pytest.raises(HTTPException) as exc:
        await accept_swap(swap.id, current_user=db.get(User, 7), db=db)
    assert exc.value.status_code == 400
    assert exc.value.detail == "Schemat har ändrats sedan förslaget"
    db.refresh(swap)
    assert swap.status == SwapStatus.PENDING
    assert db.query(ShiftOverride).count() == 0


@pytest.mark.anyio
async def test_accept_refuses_over_manual_override(db):
    swap = await _propose(db, 12, 7, OCT28, OCT29)
    db.add(ShiftOverride(user_id=7, date=OCT29, shift_code="N1", created_by=1))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        await accept_swap(swap.id, current_user=db.get(User, 7), db=db)
    assert exc.value.status_code == 400
    assert "manuell ändring" in exc.value.detail
    db.refresh(swap)
    assert swap.status == SwapStatus.PENDING
    assert _rows(db) == {(7, OCT29): ("N1", None)}


@pytest.mark.anyio
async def test_cancel_deletes_swap_rows(db):
    swap = await _accepted(db, 12, 7, OCT28, OCT29)
    await cancel_swap(swap.id, current_user=db.get(User, 12), db=db)
    db.refresh(swap)
    assert swap.status == SwapStatus.CANCELLED
    assert db.query(ShiftOverride).count() == 0


@pytest.mark.anyio
async def test_cancel_keeps_edited_row(db):
    swap = await _accepted(db, 12, 7, OCT28, OCT29)
    upsert_shift_override(
        db, user_id=7, date=OCT29, shift_code="N1", start_time=None, end_time=None, label="", created_by=1
    )
    db.commit()
    assert _rows(db)[(7, OCT29)] == ("N1", None)

    await cancel_swap(swap.id, current_user=db.get(User, 7), db=db)
    assert _rows(db) == {(7, OCT29): ("N1", None)}


@pytest.mark.anyio
async def test_cancel_legacy_swap_only_sets_status(db):
    legacy = ShiftSwap(
        requester_id=12,
        target_id=7,
        requester_date=OCT28,
        target_date=OCT29,
        requester_shift_code="N3",
        target_shift_code="N2",
        status=SwapStatus.ACCEPTED,
    )
    db.add_all([legacy, ShiftOverride(user_id=10, date=OCT28, shift_code="N2", created_by=1)])
    db.commit()
    await cancel_swap(legacy.id, current_user=db.get(User, 12), db=db)
    db.refresh(legacy)
    assert legacy.status == SwapStatus.CANCELLED
    assert _rows(db) == {(10, OCT28): ("N2", None)}


@pytest.mark.anyio
async def test_off_override_renders_as_off(db):
    await _accepted(db, 8, 10, OCT27, OCT27)
    week = _week(db, [8, 10])
    assert week[(8, OCT27)][0] in ("OFF", None)
    assert week[(10, OCT27)][0] == "N3"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("req_id", "tgt_id", "rd", "td"),
    [(12, 7, OCT28, OCT29), (8, 10, OCT27, OCT27), (8, 7, OCT27, OCT27)],
    ids=["two_day", "one_way", "mutual_same_day"],
)
async def test_override_swap_matches_legacy_swap(db, req_id, tgt_id, rd, td):
    users = [req_id, tgt_id]
    before = _week(db, users)

    swap = await _propose(db, req_id, tgt_id, rd, td)
    # Accept it the legacy way: status only, resolved through swap_map.
    swap.applied_as_override = False
    swap.status = SwapStatus.ACCEPTED
    db.commit()
    legacy = _week(db, users)
    assert legacy != before

    db.delete(swap)
    db.commit()
    assert _week(db, users) == before

    await _accepted(db, req_id, tgt_id, rd, td)
    assert _week(db, users) == legacy
