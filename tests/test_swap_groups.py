"""One proposal can hold several pairs; accept, reject and cancel act on the whole group.

Uses the rotation_session fixture (10-week test pattern). Week of Mon 26 Oct 2026:

    pos 3:  OFF N3  N3  N3  OFF OFF OC    (user 8)
    pos 7:  N2  N2  N2  N2  N2  OFF OFF   (user 7)
    pos 10: OFF OFF OFF N1  N1  N1  N1    (user 10)

Pair A: user 7 gives N2 on Sun 25 Oct and takes user 8's N3 on Sun 1 Nov.
Pair B: user 7 and user 8 swap N2 and N3 on Tue 27 Oct (same day).
"""

import datetime
import json

import pytest
from fastapi import HTTPException

from app.core import swaps
from app.database.database import ShiftOverride, ShiftSwap, SwapStatus, User, UserRole, WageType
from app.routes.shift_swap import accept_swap, cancel_swap, propose_swap, reject_swap

D = datetime.date
OCT25, NOV1, OCT27 = D(2026, 10, 25), D(2026, 11, 1), D(2026, 10, 27)
PAIR_A, PAIR_B = (OCT25, NOV1), (OCT27, OCT27)


@pytest.fixture
def db(rotation_session, monkeypatch):
    monkeypatch.setattr(swaps, "get_today", lambda: D(2026, 10, 20))
    for uid, pos in ((7, 7), (8, 3), (10, 10)):
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


async def _propose(db, pairs, req_id=7, tgt_id=8) -> list[ShiftSwap]:
    await propose_swap(
        target_id=tgt_id,
        req_date=[rd for rd, _ in pairs],
        tgt_date=[td for _, td in pairs],
        message="Hej",
        current_user=db.get(User, req_id),
        db=db,
    )
    return db.query(ShiftSwap).order_by(ShiftSwap.id).all()


def _statuses(db):
    db.expire_all()
    return [s.status for s in db.query(ShiftSwap).order_by(ShiftSwap.id)]


@pytest.mark.anyio
async def test_propose_creates_group(db):
    rows = await _propose(db, [PAIR_A, PAIR_B])
    assert len(rows) == 2
    assert rows[0].group_id and rows[0].group_id == rows[1].group_id
    assert [r.status for r in rows] == [SwapStatus.PENDING] * 2
    assert all(r.applied_as_override and r.message == "Hej" for r in rows)
    assert [(r.requester_date, r.target_date) for r in rows] == [PAIR_A, PAIR_B]


@pytest.mark.anyio
async def test_propose_refuses_shared_slot(db):
    # Both pairs use user 7 on 25 Oct.
    with pytest.raises(HTTPException) as exc:
        await _propose(db, [PAIR_A, (OCT25, OCT25)])
    assert exc.value.status_code == 400
    assert exc.value.detail.startswith("Byte 2: ")
    assert db.query(ShiftSwap).count() == 0


@pytest.mark.anyio
async def test_propose_prefixes_check_swap_reason(db):
    with pytest.raises(HTTPException) as exc:
        await _propose(db, [PAIR_A, (D(2026, 10, 30), D(2026, 10, 26))])
    assert exc.value.detail == "Byte 2: Du jobbar inte det datumet"
    assert db.query(ShiftSwap).count() == 0


@pytest.mark.anyio
async def test_propose_refuses_over_ten(db):
    with pytest.raises(HTTPException) as exc:
        await _propose(db, [PAIR_A] * 11)
    assert exc.value.status_code == 400
    assert "10" in exc.value.detail
    assert db.query(ShiftSwap).count() == 0


@pytest.mark.anyio
async def test_propose_refuses_mismatched_lists(db):
    with pytest.raises(HTTPException) as exc:
        await propose_swap(
            target_id=8, req_date=[OCT25, OCT27], tgt_date=[NOV1], message=None, current_user=db.get(User, 7), db=db
        )
    assert exc.value.status_code == 400
    assert db.query(ShiftSwap).count() == 0


@pytest.mark.anyio
async def test_accept_group_applies_all(db):
    rows = await _propose(db, [PAIR_A, PAIR_B])
    # Accepting through either row accepts the whole group.
    await accept_swap(rows[1].id, current_user=db.get(User, 8), db=db)
    assert _statuses(db) == [SwapStatus.ACCEPTED] * 2
    by_swap = {}
    for r in db.query(ShiftOverride).all():
        by_swap.setdefault(r.swap_id, set()).add((r.user_id, r.date, r.shift_code))
    assert by_swap == {
        rows[0].id: {(7, OCT25, "OFF"), (8, OCT25, "N2"), (8, NOV1, "OFF"), (7, NOV1, "N3")},
        rows[1].id: {(7, OCT27, "N3"), (8, OCT27, "N2")},
    }


@pytest.mark.anyio
async def test_accept_group_all_or_nothing(db):
    rows = await _propose(db, [PAIR_A, PAIR_B])
    rows[1].requester_shift_code = "N1"  # the rotation changed under pair 2
    db.commit()
    with pytest.raises(HTTPException) as exc:
        await accept_swap(rows[0].id, current_user=db.get(User, 8), db=db)
    assert exc.value.status_code == 400
    assert exc.value.detail == "Schemat har ändrats sedan förslaget"
    assert _statuses(db) == [SwapStatus.PENDING] * 2
    assert db.query(ShiftOverride).count() == 0


def _manual_n1_on_pair_b(db):
    db.add(ShiftOverride(user_id=7, date=OCT27, shift_code="N1", created_by=1))
    db.commit()


@pytest.mark.anyio
async def test_accept_group_over_manual_row(db):
    _manual_n1_on_pair_b(db)
    rows = await _propose(db, [PAIR_A, PAIR_B])
    await accept_swap(rows[0].id, current_user=db.get(User, 8), db=db)
    db.expire_all()
    assert db.query(ShiftOverride).filter(ShiftOverride.swap_id.is_(None)).count() == 0
    assert json.loads(rows[0].replaced_rows) == []
    assert [(r["kind"], r["shift_code"]) for r in json.loads(rows[1].replaced_rows)] == [("shift", "N1")]


@pytest.mark.anyio
async def test_accept_group_over_manual_row_all_or_nothing(db):
    _manual_n1_on_pair_b(db)
    rows = await _propose(db, [PAIR_A, PAIR_B])
    rows[0].requester_shift_code = "N1"  # the schedule changed under pair 1
    db.commit()
    with pytest.raises(HTTPException):
        await accept_swap(rows[0].id, current_user=db.get(User, 8), db=db)
    db.expire_all()
    assert _statuses(db) == [SwapStatus.PENDING] * 2
    assert [(r.user_id, r.date, r.shift_code, r.swap_id) for r in db.query(ShiftOverride)] == [(7, OCT27, "N1", None)]
    assert all(r.replaced_rows is None for r in db.query(ShiftSwap))


@pytest.mark.anyio
async def test_accept_group_refuses_when_a_row_is_not_pending(db):
    rows = await _propose(db, [PAIR_A, PAIR_B])
    rows[1].status = SwapStatus.CANCELLED
    db.commit()
    with pytest.raises(HTTPException) as exc:
        await accept_swap(rows[0].id, current_user=db.get(User, 8), db=db)
    assert exc.value.detail == "Bytet är inte längre väntande"
    assert db.query(ShiftOverride).count() == 0


@pytest.mark.anyio
async def test_accept_group_forbidden_for_requester(db):
    rows = await _propose(db, [PAIR_A, PAIR_B])
    with pytest.raises(HTTPException) as exc:
        await accept_swap(rows[0].id, current_user=db.get(User, 7), db=db)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_reject_group(db):
    rows = await _propose(db, [PAIR_A, PAIR_B])
    await reject_swap(rows[0].id, current_user=db.get(User, 8), db=db)
    assert _statuses(db) == [SwapStatus.REJECTED] * 2


@pytest.mark.anyio
async def test_cancel_group_removes_all_rows(db):
    rows = await _propose(db, [PAIR_A, PAIR_B])
    await accept_swap(rows[0].id, current_user=db.get(User, 8), db=db)
    assert db.query(ShiftOverride).count() == 6
    await cancel_swap(rows[1].id, current_user=db.get(User, 7), db=db)
    assert _statuses(db) == [SwapStatus.CANCELLED] * 2
    assert db.query(ShiftOverride).count() == 0


@pytest.mark.anyio
async def test_cancel_group_forbidden_for_outsider(db):
    rows = await _propose(db, [PAIR_A, PAIR_B])
    await accept_swap(rows[0].id, current_user=db.get(User, 8), db=db)
    with pytest.raises(HTTPException) as exc:
        await cancel_swap(rows[0].id, current_user=db.get(User, 10), db=db)
    assert exc.value.status_code == 403
    assert _statuses(db) == [SwapStatus.ACCEPTED] * 2
    assert db.query(ShiftOverride).count() == 6


@pytest.mark.anyio
async def test_single_legacy_row_unchanged(db):
    # A row without group_id is a group of one: only it moves.
    legacy = ShiftSwap(
        requester_id=7,
        target_id=8,
        requester_date=OCT25,
        target_date=NOV1,
        requester_shift_code="N2",
        target_shift_code="N3",
        status=SwapStatus.PENDING,
    )
    other = ShiftSwap(
        requester_id=8,
        target_id=10,
        requester_date=D(2026, 10, 28),
        target_date=D(2026, 10, 28),
        requester_shift_code="N3",
        target_shift_code="N1",
        status=SwapStatus.PENDING,
    )
    db.add_all([legacy, other])
    db.commit()
    await accept_swap(legacy.id, current_user=db.get(User, 8), db=db)
    assert _statuses(db) == [SwapStatus.ACCEPTED, SwapStatus.PENDING]
    assert {r.swap_id for r in db.query(ShiftOverride).all()} == {legacy.id}


def test_propose_form_reads_repeated_fields(test_client, test_user, admin_user):
    # Two req_date values against one tgt_date reach the handler as lists of different length.
    test_client.post("/login", data={"username": "testuser", "password": "testpass123"})
    resp = test_client.post(
        "/swaps/propose",
        data={"target_id": str(admin_user.id), "req_date": ["2030-01-01", "2030-01-02"], "tgt_date": "2030-01-03"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "Ogiltigt förslag" in resp.text
