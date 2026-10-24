"""A swap hands over the whole day: code, custom clock times and the on-call window.

Uses the rotation_session fixture (10-week test pattern). Week of Mon 26 Oct 2026:

    pos 7:  N2  N2  N2  N2  OFF OFF OFF   (user 7)
    pos 10: OFF OFF N1  N1  N1  N1  OC    (user 10)

Hours and on-call pay are read through generate_period_data, the path pay forecasts use.
"""

import datetime

import pytest
from fastapi import HTTPException

from app.core import swaps
from app.core.schedule import clear_schedule_cache, generate_period_data
from app.database.database import (
    OnCallOverride,
    OnCallOverrideType,
    ShiftOverride,
    ShiftSwap,
    User,
    UserRole,
    WageType,
)
from app.routes.shift_override import upsert_shift_override
from app.routes.shift_swap import accept_swap, cancel_swap, propose_swap

D = datetime.date
T = datetime.time
FRI, SUN = D(2026, 10, 30), D(2026, 11, 1)


def _user(uid, pos):
    return User(
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


@pytest.fixture
def db(rotation_session, monkeypatch):
    monkeypatch.setattr(swaps, "get_today", lambda: D(2026, 10, 20))
    rotation_session.add_all([_user(7, 7), _user(10, 10)])
    rotation_session.commit()
    return rotation_session


def _day(db, uid, day):
    """(code, hours, start, end, oncall_pay) as the pay forecast sees uid's day."""
    clear_schedule_cache()
    [info] = generate_period_data(day, day, db.get(User, uid).rotation_person_id, session=db)
    shift = info.get("shift")
    return (
        shift.code if shift else "OFF",
        round(info.get("hours") or 0.0, 2),
        info.get("start"),
        info.get("end"),
        round(info.get("oncall_pay") or 0.0, 2),
    )


async def _swap(db, req_id, tgt_id, day) -> ShiftSwap:
    """Propose and accept a same-day give-away of req_id's shift on day."""
    await propose_swap(
        target_id=tgt_id, req_date=[day], tgt_date=[day], message=None, current_user=db.get(User, req_id), db=db
    )
    swap = db.query(ShiftSwap).order_by(ShiftSwap.id.desc()).first()
    await accept_swap(swap.id, current_user=db.get(User, tgt_id), db=db)
    return swap


def _custom_n1(db):
    # User 10 works N1 on Fri 30 but stays until 16:30.
    upsert_shift_override(
        db, user_id=10, date=FRI, shift_code="N1", start_time=T(6, 0), end_time=T(16, 30), label="", created_by=10
    )
    db.commit()


def _windowed_oc(db, end="12:00"):
    # User 10 shares Sunday's on-call and covers it until noon.
    db.add(
        OnCallOverride(
            user_id=10, date=SUN, override_type=OnCallOverrideType.ADD, start_time="00:00", end_time=end, created_by=10
        )
    )
    db.commit()


@pytest.mark.anyio
async def test_custom_window_n1_moves_with_the_swap_and_back_on_cancel(db):
    _custom_n1(db)
    before = _day(db, 10, FRI)
    assert before[:2] == ("N1", 10.5)

    swap = await _swap(db, 10, 7, FRI)
    assert _day(db, 7, FRI) == before
    assert _day(db, 10, FRI)[:2] == ("OFF", 0.0)

    await cancel_swap(swap.id, current_user=db.get(User, 7), db=db)
    assert _day(db, 10, FRI) == before
    assert _day(db, 7, FRI)[:2] == ("OFF", 0.0)


@pytest.mark.anyio
async def test_windowed_on_call_moves_with_the_swap_and_back_on_cancel(db):
    whole_day_pay = _day(db, 10, SUN)[4]
    _windowed_oc(db)
    before = _day(db, 10, SUN)
    assert before[0] == "OC"
    assert 0 < before[4] < whole_day_pay

    swap = await _swap(db, 10, 7, SUN)
    assert _day(db, 7, SUN)[4] == before[4]
    assert _day(db, 7, SUN)[0] == "OC"
    assert _day(db, 10, SUN)[4] == 0.0
    owned = db.query(OnCallOverride).filter(OnCallOverride.swap_id == swap.id).all()
    assert [(r.user_id, r.start_time, r.end_time) for r in owned] == [(7, "00:00", "12:00")]

    await cancel_swap(swap.id, current_user=db.get(User, 7), db=db)
    assert _day(db, 10, SUN) == before
    assert _day(db, 7, SUN)[4] == 0.0
    rows = db.query(OnCallOverride).all()
    assert [(r.user_id, r.end_time, r.swap_id) for r in rows] == [(10, "12:00", None)]


@pytest.mark.anyio
async def test_default_times_store_no_custom_window(db):
    swap = await _swap(db, 10, 7, FRI)
    row = db.query(ShiftOverride).filter(ShiftOverride.swap_id == swap.id, ShiftOverride.user_id == 7).one()
    assert (row.shift_code, row.start_time, row.end_time) == ("N1", None, None)
    assert db.query(OnCallOverride).count() == 0


async def _propose_only(db, day):
    await propose_swap(target_id=7, req_date=[day], tgt_date=[day], message=None, current_user=db.get(User, 10), db=db)
    return db.query(ShiftSwap).order_by(ShiftSwap.id.desc()).first()


@pytest.mark.anyio
async def test_accept_refuses_when_custom_times_changed(db):
    _custom_n1(db)
    swap = await _propose_only(db, FRI)
    upsert_shift_override(
        db, user_id=10, date=FRI, shift_code="N1", start_time=T(6, 0), end_time=T(18, 0), label="", created_by=10
    )
    db.commit()
    with pytest.raises(HTTPException) as exc:
        await accept_swap(swap.id, current_user=db.get(User, 7), db=db)
    assert exc.value.detail == "Schemat har ändrats sedan förslaget"


@pytest.mark.anyio
async def test_accept_refuses_when_on_call_window_changed(db):
    _windowed_oc(db)
    swap = await _propose_only(db, SUN)
    db.query(OnCallOverride).one().end_time = "18:00"
    db.commit()
    with pytest.raises(HTTPException) as exc:
        await accept_swap(swap.id, current_user=db.get(User, 7), db=db)
    assert exc.value.detail == "Schemat har ändrats sedan förslaget"
