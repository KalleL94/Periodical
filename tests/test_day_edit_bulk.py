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
    """The flat field shape the browser posts: <field>_<ISO date>, plus each row's
    own area_<ISO date>. Every row here gets the same area; _mixed_form varies it."""
    data = {"user_id": str(user_id), "return_to": return_to}
    for date, fields in rows.items():
        data[f"area_{date.isoformat()}"] = area
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
            request=bulk_request(_form("nonsense", test_user.id, {DATES[0]: {"absence_type": "SICK"}})),
            session=test_db,
            current_user=test_user,
        )
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_editing_another_user_is_refused(test_db, test_user, bulk_request):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await bulk_edit(
            request=bulk_request(_form("absence", test_user.id + 999, {DATES[0]: {"absence_type": "SICK"}})),
            session=test_db,
            current_user=test_user,
        )
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_a_normal_shift_keeps_its_own_times(test_db, test_user, bulk_request):
    """The per-day tab must record "N1 but until 16:30" the way the day page does."""
    request = bulk_request(
        _form(
            "shift",
            test_user.id,
            {
                DATES[0]: {
                    "shift_code": "N1",
                    "shift_label": "",
                    "shift_start": "07:00",
                    "shift_end": "16:30",
                }
            },
        )
    )
    await bulk_edit(request=request, session=test_db, current_user=test_user)

    row = test_db.query(ShiftOverride).one()
    assert row.shift_code == "N1"
    assert row.start_time == datetime.time(7, 0)
    assert row.end_time == datetime.time(16, 30)


@pytest.mark.anyio
async def test_a_normal_shift_without_times_stores_none(test_db, test_user, bulk_request):
    request = bulk_request(
        _form(
            "shift",
            test_user.id,
            {DATES[0]: {"shift_code": "N1", "shift_label": "", "shift_start": "", "shift_end": ""}},
        )
    )
    await bulk_edit(request=request, session=test_db, current_user=test_user)

    row = test_db.query(ShiftOverride).one()
    assert row.start_time is None
    assert row.end_time is None


def _mixed_form(user_id, rows, return_to=""):
    """Each row carries its own area, so one post can mix them."""
    data = {"user_id": str(user_id), "return_to": return_to}
    for date, (area, fields) in rows.items():
        data[f"area_{date.isoformat()}"] = area
        for name, value in fields.items():
            data[f"{name}_{date.isoformat()}"] = value
    return data


@pytest.mark.anyio
async def test_each_day_may_choose_its_own_area(test_db, test_user, bulk_request):
    """Monday sick, Tuesday overtime, Wednesday a retimed shift, in one post."""
    request = bulk_request(
        _mixed_form(
            test_user.id,
            {
                DATES[0]: ("absence", {"absence_type": "SICK"}),
                DATES[1]: (
                    "time",
                    {
                        "time_kind": "extra",
                        "time_side": "before",
                        "time_start": "05:00",
                        "time_end": "06:00",
                        "time_hours": "1.0",
                    },
                ),
                DATES[2]: (
                    "shift",
                    {
                        "shift_code": "N1",
                        "shift_label": "",
                        "shift_start": "07:00",
                        "shift_end": "16:30",
                    },
                ),
            },
        )
    )
    await bulk_edit(request=request, session=test_db, current_user=test_user)

    assert [a.date for a in test_db.query(Absence).all()] == [DATES[0]]
    assert [r.date for r in test_db.query(OvertimeShift).all()] == [DATES[1]]
    assert [o.date for o in test_db.query(ShiftOverride).all()] == [DATES[2]]


@pytest.mark.anyio
async def test_a_rows_other_areas_are_ignored(test_db, test_user, bulk_request):
    """Every area's fields sit in every row; only the row's own area is read."""
    form = _mixed_form(
        test_user.id,
        {DATES[0]: ("absence", {"absence_type": "SICK"})},
    )
    iso = DATES[0].isoformat()
    form[f"time_kind_{iso}"] = "extra"
    form[f"time_side_{iso}"] = "before"
    form[f"time_start_{iso}"] = "05:00"
    form[f"time_end_{iso}"] = "06:00"
    form[f"time_hours_{iso}"] = "1.0"
    form[f"shift_code_{iso}"] = "N2"

    await bulk_edit(request=bulk_request(form), session=test_db, current_user=test_user)

    assert test_db.query(Absence).count() == 1
    assert test_db.query(OvertimeShift).count() == 0
    assert test_db.query(ShiftOverride).count() == 0


@pytest.mark.anyio
async def test_a_row_without_an_area_is_skipped(test_db, test_user, bulk_request):
    form = _mixed_form(test_user.id, {DATES[0]: ("", {"absence_type": "SICK"})})
    await bulk_edit(request=bulk_request(form), session=test_db, current_user=test_user)
    assert test_db.query(Absence).count() == 0


@pytest.mark.anyio
async def test_an_unknown_row_area_is_rejected(test_db, test_user, bulk_request):
    from fastapi import HTTPException

    form = _mixed_form(test_user.id, {DATES[0]: ("nonsense", {"absence_type": "SICK"})})
    with pytest.raises(HTTPException) as exc:
        await bulk_edit(request=bulk_request(form), session=test_db, current_user=test_user)
    assert exc.value.status_code == 400
