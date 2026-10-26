"""Shift, on-call and day edit messages follow the user's language."""

import datetime
from urllib.parse import unquote

import pytest
from fastapi import HTTPException

from app.database.database import OvertimeShift
from app.routes.day_edit import clear_days
from app.routes.oncall import add_oncall_override
from app.routes.shift_override import add_shift_override

D = datetime.date
DATES = [D(2026, 6, 1), D(2026, 6, 2), D(2026, 6, 3)]


def _as(test_db, user, lang):
    user.language = lang
    test_db.commit()
    return user


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("sv", "Ogiltigt skiftkod, använd N1/N2/N3/ETC"),
        ("en", "Invalid shift code, use N1/N2/N3/ETC"),
    ],
)
async def test_shift_override_bad_code(test_db, test_user, lang, expected):
    user = _as(test_db, test_user, lang)
    with pytest.raises(HTTPException) as exc:
        await add_shift_override(
            user_id=user.id,
            dates=[DATES[0]],
            shift_code="XX",
            start_time=None,
            end_time=None,
            label="",
            return_to="",
            session=test_db,
            current_user=user,
        )
    assert exc.value.detail == expected


@pytest.mark.anyio
@pytest.mark.parametrize(("lang", "expected"), [("sv", "Ogiltig tid: 25:00"), ("en", "Invalid time: 25:00")])
async def test_oncall_invalid_time(test_db, test_user, lang, expected):
    user = _as(test_db, test_user, lang)
    with pytest.raises(HTTPException) as exc:
        await add_oncall_override(
            user_id=user.id,
            dates=[DATES[0]],
            start_time="25:00",
            end_time=None,
            reason=None,
            return_to="",
            session=test_db,
            current_user=user,
        )
    assert exc.value.detail == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("sv", ["1 dagar rensade", "2 hoppades över", "hade inget att rensa"]),
        ("en", ["1 days cleared", "2 skipped", "had nothing to clear"]),
    ],
)
async def test_clear_report(test_db, test_user, lang, expected):
    user = _as(test_db, test_user, lang)
    test_db.add(
        OvertimeShift(
            user_id=user.id,
            date=DATES[0],
            start_time=datetime.time(5, 0),
            end_time=datetime.time(6, 0),
            hours=1.0,
            ot_pay=0.0,
            kind="extra",
            side="before",
        )
    )
    test_db.commit()

    response = await clear_days(
        user_id=user.id,
        dates=DATES,
        what="all",
        return_to="",
        session=test_db,
        current_user=user,
    )
    message = unquote(response.headers["location"])
    for text in expected:
        assert text in message
