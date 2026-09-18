"""Clearing a change across a calendar selection.

The existing delete routes are keyed by row id, so they cannot express "remove
the extra-time row on these seven days". Without this route a multi-day edit can
only be undone by visiting every day page.
"""

import datetime

import pytest

from app.database.database import (
    Absence,
    AbsenceType,
    OnCallOverride,
    OnCallOverrideType,
    OvertimeShift,
    ShiftOverride,
)
from app.routes.day_edit import clear_days

D = datetime.date
DATES = [D(2026, 6, 1), D(2026, 6, 2), D(2026, 6, 3)]


def _overtime(user_id, date, kind="extra", side="before"):
    return OvertimeShift(
        user_id=user_id,
        date=date,
        start_time=datetime.time(5, 0),
        end_time=datetime.time(6, 0),
        hours=1.0,
        ot_pay=0.0,
        kind=kind,
        side=side,
    )


@pytest.mark.anyio
async def test_clearing_extra_time_removes_only_that_kind_and_side(test_db, test_user):
    for date in DATES:
        test_db.add(_overtime(test_user.id, date))
    test_db.add(_overtime(test_user.id, DATES[0], kind="ot", side="after"))
    test_db.commit()

    await clear_days(
        user_id=test_user.id,
        dates=DATES,
        what="extra:before",
        return_to="",
        session=test_db,
        current_user=test_user,
    )

    rows = test_db.query(OvertimeShift).all()
    assert len(rows) == 1
    assert (rows[0].kind, rows[0].side) == ("ot", "after")


@pytest.mark.anyio
async def test_clearing_reports_days_that_had_nothing(test_db, test_user):
    test_db.add(_overtime(test_user.id, DATES[0]))
    test_db.commit()

    response = await clear_days(
        user_id=test_user.id,
        dates=DATES,
        what="extra:before",
        return_to="/week/1?year=2026&week=23",
        session=test_db,
        current_user=test_user,
    )
    location = response.headers["location"]
    assert location.startswith("/week/1")
    assert "success=" in location


@pytest.mark.anyio
async def test_clearing_absence_oncall_and_shift(test_db, test_user):
    date = DATES[0]
    test_db.add(Absence(user_id=test_user.id, date=date, absence_type=AbsenceType.SICK))
    test_db.add(
        OnCallOverride(
            user_id=test_user.id,
            date=date,
            override_type=OnCallOverrideType.ADD,
            created_by=test_user.id,
        )
    )
    test_db.add(ShiftOverride(user_id=test_user.id, date=date, shift_code="N2", created_by=test_user.id))
    test_db.commit()

    for what in ("absence", "oncall", "shift"):
        await clear_days(
            user_id=test_user.id,
            dates=[date],
            what=what,
            return_to="",
            session=test_db,
            current_user=test_user,
        )

    assert test_db.query(Absence).count() == 0
    assert test_db.query(OnCallOverride).count() == 0
    assert test_db.query(ShiftOverride).count() == 0


@pytest.mark.anyio
async def test_an_unknown_target_is_rejected(test_db, test_user):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await clear_days(
            user_id=test_user.id,
            dates=DATES,
            what="nonsense",
            return_to="",
            session=test_db,
            current_user=test_user,
        )
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_clearing_another_user_is_refused(test_db, test_user):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await clear_days(
            user_id=test_user.id + 999,
            dates=DATES,
            what="absence",
            return_to="",
            session=test_db,
            current_user=test_user,
        )
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_the_report_says_cleared_not_set(test_db, test_user):
    """ "3 dagar satta" after a clear reads as the opposite of what happened."""
    from urllib.parse import unquote

    for date in DATES:
        test_db.add(_overtime(test_user.id, date))
    test_db.commit()

    response = await clear_days(
        user_id=test_user.id,
        dates=DATES,
        what="extra:before",
        return_to="/week/1?year=2026&week=23",
        session=test_db,
        current_user=test_user,
    )
    message = unquote(response.headers["location"])
    assert "rensade" in message
    assert "satta" not in message


@pytest.mark.anyio
async def test_all_clears_every_kind_on_the_selected_days(test_db, test_user):
    """One pass to undo a day, instead of picking each type in turn."""
    date = DATES[0]
    test_db.add(_overtime(test_user.id, date))
    test_db.add(_overtime(test_user.id, date, kind="ot", side="after"))
    test_db.add(Absence(user_id=test_user.id, date=date, absence_type=AbsenceType.SICK))
    test_db.add(
        OnCallOverride(
            user_id=test_user.id,
            date=date,
            override_type=OnCallOverrideType.ADD,
            created_by=test_user.id,
        )
    )
    test_db.add(ShiftOverride(user_id=test_user.id, date=date, shift_code="N2", created_by=test_user.id))
    test_db.commit()

    await clear_days(
        user_id=test_user.id,
        dates=[date],
        what="all",
        return_to="",
        session=test_db,
        current_user=test_user,
    )

    assert test_db.query(OvertimeShift).count() == 0
    assert test_db.query(Absence).count() == 0
    assert test_db.query(OnCallOverride).count() == 0
    assert test_db.query(ShiftOverride).count() == 0


@pytest.mark.anyio
async def test_all_leaves_other_days_alone(test_db, test_user):
    test_db.add(_overtime(test_user.id, DATES[0]))
    test_db.add(_overtime(test_user.id, DATES[2]))
    test_db.commit()

    await clear_days(
        user_id=test_user.id,
        dates=[DATES[0]],
        what="all",
        return_to="",
        session=test_db,
        current_user=test_user,
    )

    rows = test_db.query(OvertimeShift).all()
    assert [r.date for r in rows] == [DATES[2]]


@pytest.mark.anyio
async def test_all_reports_a_day_that_had_nothing(test_db, test_user):
    from urllib.parse import unquote

    test_db.add(_overtime(test_user.id, DATES[0]))
    test_db.commit()

    response = await clear_days(
        user_id=test_user.id,
        dates=DATES,
        what="all",
        return_to="/week/1?year=2026&week=23",
        session=test_db,
        current_user=test_user,
    )
    message = unquote(response.headers["location"])
    assert "1 dagar rensade" in message
    assert "2 hoppades över" in message
