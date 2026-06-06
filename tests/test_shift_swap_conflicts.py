import datetime

import pytest
from fastapi import HTTPException

from app.auth.auth import get_password_hash
from app.database.database import ShiftSwap, SwapStatus, User, UserRole
from app.routes.shift_swap import accept_swap, propose_swap


def _add_user(db, user_id: int, username: str, person_id: int) -> User:
    user = User(
        id=user_id,
        username=username,
        password_hash=get_password_hash("testpass123"),
        name=f"User {user_id}",
        role=UserRole.USER,
        wage=35000,
        vacation={},
        must_change_password=0,
        person_id=person_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.mark.anyio
async def test_propose_swap_rejects_conflicting_pending_slot(test_db, test_user, admin_user):
    existing = ShiftSwap(
        requester_id=test_user.id,
        target_id=admin_user.id,
        requester_date=datetime.date(2026, 7, 10),
        target_date=datetime.date(2026, 7, 11),
        requester_shift_code="N1",
        target_shift_code="N2",
        status=SwapStatus.PENDING,
    )
    test_db.add(existing)
    test_db.commit()

    with pytest.raises(HTTPException) as exc:
        await propose_swap(
            target_id=admin_user.id,
            requester_date="2026-07-10",
            target_date="2026-07-12",
            message=None,
            current_user=test_user,
            db=test_db,
        )

    assert exc.value.status_code == 400
    assert "byte finns redan" in exc.value.detail
    assert test_db.query(ShiftSwap).count() == 1


@pytest.mark.anyio
async def test_accept_swap_rejects_conflicting_accepted_slot(test_db, test_user, admin_user):
    other_user = _add_user(test_db, 3, "other", 3)
    accepted = ShiftSwap(
        requester_id=other_user.id,
        target_id=test_user.id,
        requester_date=datetime.date(2026, 7, 10),
        target_date=datetime.date(2026, 7, 11),
        requester_shift_code="N1",
        target_shift_code="N2",
        status=SwapStatus.ACCEPTED,
    )
    pending = ShiftSwap(
        requester_id=admin_user.id,
        target_id=test_user.id,
        requester_date=datetime.date(2026, 7, 10),
        target_date=datetime.date(2026, 7, 12),
        requester_shift_code="N3",
        target_shift_code="N1",
        status=SwapStatus.PENDING,
    )
    test_db.add_all([accepted, pending])
    test_db.commit()

    with pytest.raises(HTTPException) as exc:
        await accept_swap(pending.id, current_user=test_user, db=test_db)

    assert exc.value.status_code == 400
    assert "byte finns redan" in exc.value.detail
    test_db.refresh(pending)
    assert pending.status == SwapStatus.PENDING


@pytest.mark.anyio
async def test_accept_swap_allows_non_conflicting_pending_swap(test_db, test_user, admin_user):
    pending = ShiftSwap(
        requester_id=admin_user.id,
        target_id=test_user.id,
        requester_date=datetime.date(2026, 7, 10),
        target_date=datetime.date(2026, 7, 12),
        requester_shift_code="N3",
        target_shift_code="N1",
        status=SwapStatus.PENDING,
    )
    test_db.add(pending)
    test_db.commit()

    response = await accept_swap(pending.id, current_user=test_user, db=test_db)

    test_db.refresh(pending)
    assert pending.status == SwapStatus.ACCEPTED
    assert pending.responded_at is not None
    assert response.status_code == 303
