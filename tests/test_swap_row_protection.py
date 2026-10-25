"""Rows written by an accepted swap can only be undone by cancelling the swap.

A participant who edits or deletes one through the ordinary override routes
would dismantle half a swap. An admin may: an edit turns the row manual.
"""

import datetime
import json

import pytest

from app.auth.auth import create_access_token
from app.database.database import OnCallOverride, OnCallOverrideType, ShiftOverride, ShiftSwap, SwapStatus

DAY = datetime.date(2026, 11, 2)
REFUSAL = "Passet hör till ett byte, avbryt bytet i stället"


@pytest.fixture
def swap_row(test_db, test_user, admin_user):
    swap = ShiftSwap(
        requester_id=admin_user.id,
        target_id=test_user.id,
        requester_date=DAY,
        target_date=DAY,
        requester_shift_code="N2",
        target_shift_code="OFF",
        status=SwapStatus.ACCEPTED,
        applied_as_override=True,
    )
    test_db.add(swap)
    test_db.commit()
    row = ShiftOverride(user_id=test_user.id, date=DAY, shift_code="N2", swap_id=swap.id, created_by=test_user.id)
    test_db.add(row)
    test_db.commit()
    return row


def _login(client, user):
    token = create_access_token(data={"sub": str(user.id)})
    client.cookies.set("access_token", f"Bearer {token}")
    return client


@pytest.fixture
def as_user(test_client, test_user):
    return _login(test_client, test_user)


@pytest.fixture
def as_admin(test_client, admin_user):
    return _login(test_client, admin_user)


def _assert_untouched(test_db, row):
    test_db.expire_all()
    fresh = test_db.get(ShiftOverride, row.id)
    assert fresh is not None
    assert (fresh.shift_code, fresh.swap_id) == ("N2", row.swap_id)


def _assert_refused(response):
    assert response.status_code == 400
    assert REFUSAL in response.text


def test_participant_delete_refused(as_user, test_db, swap_row):
    r = as_user.post(f"/shift-override/{swap_row.id}/delete", follow_redirects=False)
    _assert_refused(r)
    _assert_untouched(test_db, swap_row)


def test_participant_add_over_swap_row_refused(as_user, test_db, test_user, swap_row):
    r = as_user.post(
        "/shift-override/add",
        data={"user_id": test_user.id, "dates": [DAY.isoformat()], "shift_code": "N1"},
        follow_redirects=False,
    )
    _assert_refused(r)
    _assert_untouched(test_db, swap_row)


@pytest.mark.parametrize("what", ["shift", "all"])
def test_participant_clear_refused(as_user, test_db, test_user, swap_row, what):
    r = as_user.post(
        "/day-edit/clear",
        data={"user_id": test_user.id, "dates": [DAY.isoformat()], "what": what},
        follow_redirects=False,
    )
    _assert_refused(r)
    _assert_untouched(test_db, swap_row)


def test_participant_bulk_edit_refused(as_user, test_db, test_user, swap_row):
    d = DAY.isoformat()
    r = as_user.post(
        "/day-edit/bulk",
        data={"user_id": test_user.id, f"area_{d}": "shift", f"shift_code_{d}": "N1"},
        follow_redirects=False,
    )
    _assert_refused(r)
    _assert_untouched(test_db, swap_row)


def test_admin_edit_detaches_and_cancel_keeps_it(as_admin, test_db, test_user, swap_row):
    swap_id = swap_row.swap_id
    r = as_admin.post(
        "/shift-override/add",
        data={"user_id": test_user.id, "dates": [DAY.isoformat()], "shift_code": "N1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    test_db.expire_all()
    row = test_db.get(ShiftOverride, swap_row.id)
    assert (row.shift_code, row.swap_id) == ("N1", None)

    r = as_admin.post(f"/swaps/{swap_id}/cancel", follow_redirects=False)
    assert r.status_code == 303
    test_db.expire_all()
    assert test_db.get(ShiftSwap, swap_id).status == SwapStatus.CANCELLED
    assert test_db.get(ShiftOverride, swap_row.id).shift_code == "N1"


def test_admin_delete_removes_row(as_admin, test_db, swap_row):
    row_id = swap_row.id
    r = as_admin.post(f"/shift-override/{row_id}/delete", follow_redirects=False)
    assert r.status_code == 303
    test_db.expire_all()
    assert test_db.get(ShiftOverride, row_id) is None


def test_participant_oncall_add_over_swap_row_refused(as_user, test_db, test_user, swap_row):
    r = as_user.post(
        "/oncall/add",
        data={"user_id": test_user.id, "dates": [DAY.isoformat()]},
        follow_redirects=False,
    )
    _assert_refused(r)
    assert test_db.query(OnCallOverride).count() == 0


def test_participant_oncall_remove_over_swap_row_refused(as_user, test_db, test_user, swap_row):
    r = as_user.post(
        "/oncall/remove",
        data={"user_id": test_user.id, "date": DAY.isoformat()},
        follow_redirects=False,
    )
    _assert_refused(r)
    assert test_db.query(OnCallOverride).count() == 0


def test_participant_oncall_clear_over_swap_row_refused(as_user, test_db, test_user, swap_row):
    r = as_user.post(
        "/day-edit/clear",
        data={"user_id": test_user.id, "dates": [DAY.isoformat()], "what": "oncall"},
        follow_redirects=False,
    )
    _assert_refused(r)


def test_admin_oncall_add_over_swap_row_allowed(as_admin, test_db, test_user, swap_row):
    r = as_admin.post(
        "/oncall/add",
        data={"user_id": test_user.id, "dates": [DAY.isoformat()]},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert test_db.query(OnCallOverride).count() == 1
    _assert_untouched(test_db, swap_row)


@pytest.fixture
def swap_oncall_row(test_db, test_user, swap_row):
    row = OnCallOverride(
        user_id=test_user.id,
        date=DAY,
        override_type=OnCallOverrideType.ADD,
        start_time="00:00",
        end_time="12:00",
        swap_id=swap_row.swap_id,
        created_by=test_user.id,
    )
    test_db.add(row)
    test_db.commit()
    return row


def test_participant_oncall_delete_of_swap_row_refused(as_user, test_db, swap_oncall_row):
    r = as_user.post(f"/oncall/{swap_oncall_row.id}/delete", follow_redirects=False)
    _assert_refused(r)
    test_db.expire_all()
    assert test_db.get(OnCallOverride, swap_oncall_row.id) is not None


def test_admin_oncall_edit_detaches_swap_row(as_admin, test_db, test_user, swap_oncall_row):
    r = as_admin.post(
        "/oncall/add",
        data={"user_id": test_user.id, "dates": [DAY.isoformat()]},
        follow_redirects=False,
    )
    assert r.status_code == 303
    test_db.expire_all()
    assert test_db.get(OnCallOverride, swap_oncall_row.id).swap_id is None


# The slot held manual rows before the swap; cancel would put them back.
_SNAPSHOT = [
    {
        "kind": "shift",
        "date": DAY.isoformat(),
        "shift_code": "N1",
        "start_time": None,
        "end_time": None,
        "label": None,
        "created_by": None,
    },
    {
        "kind": "oncall",
        "date": DAY.isoformat(),
        "override_type": "REMOVE",
        "start_time": None,
        "end_time": None,
        "reason": "Manual",
        "created_by": None,
    },
]


@pytest.fixture
def snapshotted(test_db, test_user, swap_oncall_row):
    swap = test_db.get(ShiftSwap, swap_oncall_row.swap_id)
    swap.replaced_rows = json.dumps([{**fields, "user_id": test_user.id} for fields in _SNAPSHOT])
    test_db.commit()
    return swap.id


def _cancel(client, swap_id):
    r = client.post(f"/swaps/{swap_id}/cancel", follow_redirects=False)
    assert r.status_code == 303
    return r


def test_cancel_restores_snapshot_when_admin_left_slot_alone(as_admin, test_db, snapshotted):
    _cancel(as_admin, snapshotted)
    test_db.expire_all()
    assert test_db.query(ShiftOverride).one().shift_code == "N1"
    assert test_db.query(OnCallOverride).one().override_type == OnCallOverrideType.REMOVE


def test_admin_shift_delete_survives_cancel(as_admin, test_db, swap_row, snapshotted):
    r = as_admin.post(f"/shift-override/{swap_row.id}/delete", follow_redirects=False)
    assert r.status_code == 303
    _cancel(as_admin, snapshotted)
    test_db.expire_all()
    assert test_db.query(ShiftOverride).count() == 0
    # The on-call snapshot was not touched by the shift delete.
    assert test_db.query(OnCallOverride).one().override_type == OnCallOverrideType.REMOVE


def test_admin_oncall_delete_survives_cancel(as_admin, test_db, swap_oncall_row, snapshotted):
    r = as_admin.post(f"/oncall/{swap_oncall_row.id}/delete", follow_redirects=False)
    assert r.status_code == 303
    _cancel(as_admin, snapshotted)
    test_db.expire_all()
    assert test_db.query(OnCallOverride).count() == 0
    assert test_db.query(ShiftOverride).one().shift_code == "N1"


@pytest.mark.parametrize("what", ["shift", "oncall", "all"])
def test_admin_clear_survives_cancel(as_admin, test_db, test_user, snapshotted, what):
    r = as_admin.post(
        "/day-edit/clear",
        data={"user_id": test_user.id, "dates": [DAY.isoformat()], "what": what},
        follow_redirects=False,
    )
    assert r.status_code == 303
    _cancel(as_admin, snapshotted)
    test_db.expire_all()
    shift_left = test_db.query(ShiftOverride).count()
    oncall_left = test_db.query(OnCallOverride).count()
    assert (shift_left, oncall_left) == {"shift": (0, 1), "oncall": (1, 0), "all": (0, 0)}[what]


def test_admin_edit_then_delete_survives_cancel(as_admin, test_db, test_user, swap_row, snapshotted):
    r = as_admin.post(
        "/shift-override/add",
        data={"user_id": test_user.id, "dates": [DAY.isoformat()], "shift_code": "N2"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = as_admin.post(f"/shift-override/{swap_row.id}/delete", follow_redirects=False)
    assert r.status_code == 303
    _cancel(as_admin, snapshotted)
    test_db.expire_all()
    assert test_db.query(ShiftOverride).count() == 0


def test_admin_oncall_remove_then_delete_survives_cancel(as_admin, test_db, test_user, swap_oncall_row, snapshotted):
    r = as_admin.post(
        "/oncall/remove",
        data={"user_id": test_user.id, "date": DAY.isoformat(), "reason": "Admin"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = as_admin.post(f"/oncall/{swap_oncall_row.id}/delete", follow_redirects=False)
    assert r.status_code == 303
    _cancel(as_admin, snapshotted)
    test_db.expire_all()
    assert test_db.query(OnCallOverride).count() == 0
