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
        is_extension=False,
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
    assert shifts[0].is_extension is False
    assert response.status_code == 303


@pytest.mark.anyio
async def test_add_overtime_updates_existing_shift_for_same_user_and_date(test_db, test_user):
    await add_overtime_shift(
        user_id=test_user.id,
        date=datetime.date(2026, 1, 15),
        start_time=datetime.time(6, 0),
        end_time=datetime.time(14, 0),
        hours=8.0,
        is_extension=False,
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
        is_extension=True,
        session=test_db,
        current_user=test_user,
    )

    shifts = test_db.query(OvertimeShift).all()
    assert len(shifts) == 1
    assert shifts[0].id == original_id
    assert shifts[0].start_time == datetime.time(14, 0)
    assert shifts[0].end_time == datetime.time(22, 0)
    assert shifts[0].hours == 7.5
    assert shifts[0].is_extension is True
    assert shifts[0].ot_pay != original_pay


@pytest.mark.anyio
async def test_add_overtime_keeps_different_dates_separate(test_db, test_user):
    await add_overtime_shift(
        user_id=test_user.id,
        date=datetime.date(2026, 1, 15),
        start_time=datetime.time(6, 0),
        end_time=datetime.time(14, 0),
        hours=8.0,
        is_extension=False,
        session=test_db,
        current_user=test_user,
    )
    await add_overtime_shift(
        user_id=test_user.id,
        date=datetime.date(2026, 1, 16),
        start_time=datetime.time(14, 0),
        end_time=datetime.time(22, 0),
        hours=8.0,
        is_extension=False,
        session=test_db,
        current_user=test_user,
    )

    dates = {shift.date for shift in test_db.query(OvertimeShift).all()}
    assert dates == {datetime.date(2026, 1, 15), datetime.date(2026, 1, 16)}


@pytest.mark.anyio
async def test_add_overtime_cleans_up_legacy_duplicates(test_db, test_user):
    target_date = datetime.date(2026, 1, 15)
    first = OvertimeShift(
        user_id=test_user.id,
        date=target_date,
        start_time=datetime.time(6, 0),
        end_time=datetime.time(14, 0),
        hours=8.0,
        ot_pay=100.0,
        is_extension=False,
        created_by=test_user.id,
    )
    duplicate = OvertimeShift(
        user_id=test_user.id,
        date=target_date,
        start_time=datetime.time(14, 0),
        end_time=datetime.time(22, 0),
        hours=8.0,
        ot_pay=200.0,
        is_extension=False,
        created_by=test_user.id,
    )
    test_db.add_all([first, duplicate])
    test_db.commit()

    await add_overtime_shift(
        user_id=test_user.id,
        date=datetime.date(2026, 1, 15),
        start_time=datetime.time(22, 0),
        end_time=datetime.time(6, 0),
        hours=8.5,
        is_extension=False,
        session=test_db,
        current_user=test_user,
    )

    shifts = test_db.query(OvertimeShift).all()
    assert len(shifts) == 1
    assert shifts[0].id == first.id
    assert shifts[0].start_time == datetime.time(22, 0)
    assert shifts[0].end_time == datetime.time(6, 0)
    assert shifts[0].hours == 8.5
