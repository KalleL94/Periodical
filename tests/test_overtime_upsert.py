import datetime

import pytest

from app.database.database import OvertimeShift
from app.routes.overtime import add_overtime_shift


@pytest.mark.anyio
async def test_add_overtime_creates_shift(test_db, test_user):
    response = await add_overtime_shift(
        user_id=test_user.id,
        date=datetime.date(2026, 1, 15),
        start_time=datetime.time(6, 0),
        end_time=datetime.time(14, 0),
        hours=8.0,
        kind="ot",
        side="full",
        session=test_db,
        current_user=test_user,
    )

    shifts = test_db.query(OvertimeShift).all()
    assert len(shifts) == 1
    assert shifts[0].user_id == test_user.id
    assert shifts[0].date == datetime.date(2026, 1, 15)
    assert shifts[0].start_time == datetime.time(6, 0)
    assert shifts[0].end_time == datetime.time(14, 0)
    assert shifts[0].hours == 8.0
    assert shifts[0].side == "full"
    assert response.status_code == 303


@pytest.mark.anyio
async def test_add_overtime_updates_existing_shift_for_same_kind_and_side(test_db, test_user):
    """The upsert key is (user, date, kind, side), not (user, date)."""
    await add_overtime_shift(
        user_id=test_user.id,
        date=datetime.date(2026, 1, 15),
        start_time=datetime.time(6, 0),
        end_time=datetime.time(14, 0),
        hours=8.0,
        kind="ot",
        side="full",
        session=test_db,
        current_user=test_user,
    )
    original = test_db.query(OvertimeShift).one()
    original_id = original.id
    original_pay = original.ot_pay

    await add_overtime_shift(
        user_id=test_user.id,
        date=datetime.date(2026, 1, 15),
        start_time=datetime.time(14, 0),
        end_time=datetime.time(22, 0),
        hours=7.5,
        kind="ot",
        side="full",
        session=test_db,
        current_user=test_user,
    )

    shifts = test_db.query(OvertimeShift).all()
    assert len(shifts) == 1
    assert shifts[0].id == original_id
    assert shifts[0].start_time == datetime.time(14, 0)
    assert shifts[0].hours == 7.5
    assert shifts[0].ot_pay != original_pay


@pytest.mark.anyio
async def test_add_overtime_keeps_different_dates_separate(test_db, test_user):
    await add_overtime_shift(
        user_id=test_user.id,
        date=datetime.date(2026, 1, 15),
        start_time=datetime.time(6, 0),
        end_time=datetime.time(14, 0),
        hours=8.0,
        kind="ot",
        side="full",
        session=test_db,
        current_user=test_user,
    )
    await add_overtime_shift(
        user_id=test_user.id,
        date=datetime.date(2026, 1, 16),
        start_time=datetime.time(14, 0),
        end_time=datetime.time(22, 0),
        hours=8.0,
        kind="ot",
        side="full",
        session=test_db,
        current_user=test_user,
    )

    dates = {shift.date for shift in test_db.query(OvertimeShift).all()}
    assert dates == {datetime.date(2026, 1, 15), datetime.date(2026, 1, 16)}


@pytest.mark.anyio
async def test_two_sides_on_one_day_coexist(test_db, test_user):
    for side, start, end in (
        ("before", datetime.time(5, 0), datetime.time(6, 0)),
        ("after", datetime.time(22, 30), datetime.time(0, 30)),
    ):
        await add_overtime_shift(
            user_id=test_user.id,
            date=datetime.date(2026, 1, 15),
            start_time=start,
            end_time=end,
            hours=1.0,
            kind="ot",
            side=side,
            session=test_db,
            current_user=test_user,
        )
    shifts = test_db.query(OvertimeShift).all()
    assert {s.side for s in shifts} == {"before", "after"}


@pytest.mark.anyio
async def test_extra_time_is_stored_with_zero_ot_pay(test_db, test_user):
    """Extra time is worked time, priced through the day's segments, not the OT rate."""
    await add_overtime_shift(
        user_id=test_user.id,
        date=datetime.date(2026, 1, 15),
        start_time=datetime.time(13, 0),
        end_time=datetime.time(14, 0),
        hours=1.0,
        kind="extra",
        side="before",
        session=test_db,
        current_user=test_user,
    )
    row = test_db.query(OvertimeShift).one()
    assert row.kind == "extra"
    assert row.ot_pay == 0.0


@pytest.mark.anyio
async def test_a_duplicate_kind_and_side_is_rejected_by_the_index(test_db, test_user):
    """The route upserts, so this can only happen on a direct insert. The unique
    index declared on the model is what stops it."""
    from sqlalchemy.exc import IntegrityError

    for _ in range(2):
        test_db.add(
            OvertimeShift(
                user_id=test_user.id,
                date=datetime.date(2026, 1, 15),
                start_time=datetime.time(6, 0),
                end_time=datetime.time(14, 0),
                hours=8.0,
                ot_pay=0.0,
                kind="ot",
                side="full",
                created_by=test_user.id,
            )
        )
    with pytest.raises(IntegrityError):
        test_db.commit()


@pytest.mark.anyio
async def test_an_invalid_kind_is_rejected(test_db, test_user):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await add_overtime_shift(
            user_id=test_user.id,
            date=datetime.date(2026, 1, 15),
            start_time=datetime.time(6, 0),
            end_time=datetime.time(14, 0),
            hours=8.0,
            kind="nonsense",
            side="full",
            session=test_db,
            current_user=test_user,
        )
    assert exc.value.status_code == 400
