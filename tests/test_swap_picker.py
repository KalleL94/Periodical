"""The day picker marks days check_swap refuses instead of offering them.

Uses the rotation_session fixture (10-week test pattern). Week of Mon 26 Oct 2026:

    pos 3: OFF N3  N3  N3  OFF OFF OC    (user 8)
    pos 5: N2  N2  N2  OFF OFF N1  N1    (user 11)
    pos 7: N2  N2  N2  N2  N2  OFF OFF   (user 7)
"""

import datetime
import json

import pytest

from app.core import swaps
from app.database.database import ShiftSwap, SwapStatus, User, UserRole, WageType
from app.routes import shift_swap
from app.routes.shift_swap import get_user_shifts

D = datetime.date
SLOT_TAKEN = "Ett byte finns redan för någon av personerna på dessa datum"


@pytest.fixture
def db(rotation_session, monkeypatch):
    for module in (swaps, shift_swap):
        monkeypatch.setattr(module, "get_today", lambda: D(2026, 10, 20))
    for uid, pos in ((7, 7), (8, 3), (11, 5)):
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


async def _picker(db, requester_id, target_id, ref_date, offering):
    resp = await get_user_shifts(
        target_id, offering=offering, ref_date=ref_date, current_user=db.get(User, requester_id), db=db
    )
    return {s["date"]: s for s in json.loads(resp.body)["shifts"]}


@pytest.mark.anyio
async def test_picker_marks_slot_conflict(db):
    # Mirrors prod swap 22: user 8 already swapped 29 Oct with user 11 (legacy, swap_map).
    db.add(
        ShiftSwap(
            requester_id=8,
            target_id=11,
            requester_date=D(2026, 10, 29),
            target_date=D(2026, 10, 29),
            requester_shift_code="OFF",
            target_shift_code="N3",
            status=SwapStatus.ACCEPTED,
        )
    )
    db.commit()
    shifts = await _picker(db, 8, 7, D(2026, 10, 29), "N3")
    assert shifts["2026-10-29"]["code"] == "N2"
    assert shifts["2026-10-29"]["blocked"] == SLOT_TAKEN


@pytest.mark.anyio
async def test_picker_free_day_not_blocked(db):
    shifts = await _picker(db, 8, 7, D(2026, 10, 27), "N3")
    assert shifts["2026-10-27"]["code"] == "N2"
    assert shifts["2026-10-27"]["blocked"] is None
