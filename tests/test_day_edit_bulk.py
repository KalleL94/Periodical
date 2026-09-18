"""Different values per day in one post.

The drawer applies one value to every selected day. This is the case it cannot
express: sick on Monday, child care on Wednesday, overtime on Friday.
"""

import datetime

import pytest

from app.database.database import Absence, AbsenceType, OvertimeShift, ShiftOverride
from app.routes.day_edit import bulk_edit

D = datetime.date
DATES = [D(2026, 6, 1), D(2026, 6, 2), D(2026, 6, 3)]


def _form(area, user_id, rows, return_to=""):
    """The flat field shape the browser posts: <area>_<field>_<ISO date>."""
    data = {"user_id": str(user_id), "area": area, "return_to": return_to}
    for date, fields in rows.items():
        for name, value in fields.items():
            data[f"{name}_{date.isoformat()}"] = value
    return data


@pytest.fixture
def bulk_request():
    """A Request stand-in whose form() returns the given dict.

    The route reads the raw form because the field names carry their date, which
    keeps rows from relying on parallel arrays staying aligned.
    """

    def build(data):
        class _Request:
            async def form(self):
                return data

        return _Request()

    return build


@pytest.mark.anyio
async def test_each_day_gets_its_own_absence_type(test_db, test_user, bulk_request):
    request = bulk_request(
        _form(
            "absence",
            test_user.id,
            {
                DATES[0]: {"absence_type": "SICK", "absence_arrived": "", "absence_left": ""},
                DATES[1]: {"absence_type": "VAB", "absence_arrived": "", "absence_left": ""},
                DATES[2]: {"absence_type": "", "absence_arrived": "", "absence_left": ""},
            },
        )
    )
    await bulk_edit(request=request, session=test_db, current_user=test_user)

    rows = {a.date: a.absence_type for a in test_db.query(Absence).all()}
    assert rows == {DATES[0]: AbsenceType.SICK, DATES[1]: AbsenceType.VAB}


@pytest.mark.anyio
async def test_a_blank_row_writes_nothing(test_db, test_user, bulk_request):
    request = bulk_request(
        _form(
            "absence",
            test_user.id,
            {d: {"absence_type": "", "absence_arrived": "", "absence_left": ""} for d in DATES},
        )
    )
    await bulk_edit(request=request, session=test_db, current_user=test_user)
    assert test_db.query(Absence).count() == 0


@pytest.mark.anyio
async def test_only_the_posted_area_is_read(test_db, test_user, bulk_request):
    """Switching tab leaves the other areas' fields in the DOM; they still submit."""
    form = _form(
        "absence",
        test_user.id,
        {DATES[0]: {"absence_type": "SICK", "absence_arrived": "", "absence_left": ""}},
    )
    iso = DATES[0].isoformat()
    form[f"time_kind_{iso}"] = "extra"
    form[f"time_side_{iso}"] = "before"
    form[f"time_start_{iso}"] = "05:00"
    form[f"time_end_{iso}"] = "06:00"
    form[f"time_hours_{iso}"] = "1.0"

    await bulk_edit(request=bulk_request(form), session=test_db, current_user=test_user)

    assert test_db.query(Absence).count() == 1
    assert test_db.query(OvertimeShift).count() == 0


@pytest.mark.anyio
async def test_each_day_gets_its_own_time_row(test_db, test_user, bulk_request):
    request = bulk_request(
        _form(
            "time",
            test_user.id,
            {
                DATES[0]: {
                    "time_kind": "extra",
                    "time_side": "before",
                    "time_start": "05:00",
                    "time_end": "06:00",
                    "time_hours": "1.0",
                },
                DATES[1]: {
                    "time_kind": "ot",
                    "time_side": "after",
                    "time_start": "22:30",
                    "time_end": "00:30",
                    "time_hours": "2.0",
                },
                DATES[2]: {
                    "time_kind": "",
                    "time_side": "",
                    "time_start": "",
                    "time_end": "",
                    "time_hours": "",
                },
            },
        )
    )
    await bulk_edit(request=request, session=test_db, current_user=test_user)

    rows = {r.date: (r.kind, r.side, r.hours) for r in test_db.query(OvertimeShift).all()}
    assert rows == {DATES[0]: ("extra", "before", 1.0), DATES[1]: ("ot", "after", 2.0)}


@pytest.mark.anyio
async def test_each_day_gets_its_own_shift_code(test_db, test_user, bulk_request):
    request = bulk_request(
        _form(
            "shift",
            test_user.id,
            {
                DATES[0]: {"shift_code": "N1", "shift_label": "", "shift_start": "", "shift_end": ""},
                DATES[1]: {
                    "shift_code": "ETC",
                    "shift_label": "Kurs",
                    "shift_start": "09:00",
                    "shift_end": "12:00",
                },
                DATES[2]: {"shift_code": "", "shift_label": "", "shift_start": "", "shift_end": ""},
            },
        )
    )
    await bulk_edit(request=request, session=test_db, current_user=test_user)

    rows = {o.date: (o.shift_code, o.label) for o in test_db.query(ShiftOverride).all()}
    assert rows == {DATES[0]: ("N1", None), DATES[1]: ("ETC", "Kurs")}


@pytest.mark.anyio
async def test_an_unknown_area_is_rejected(test_db, test_user, bulk_request):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await bulk_edit(
            request=bulk_request(_form("nonsense", test_user.id, {})),
            session=test_db,
            current_user=test_user,
        )
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_editing_another_user_is_refused(test_db, test_user, bulk_request):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await bulk_edit(
            request=bulk_request(_form("absence", test_user.id + 999, {})),
            session=test_db,
            current_user=test_user,
        )
    assert exc.value.status_code == 403
