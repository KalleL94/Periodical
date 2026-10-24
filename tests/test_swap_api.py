"""Read endpoints behind the swap compare screen: /swaps/api/check, compare and day-takers.

Rotation from the 10-week test pattern, Sun 25 to Sat 31 Oct 2026:

    pos 3:  OFF N3  N3  N3  OFF OFF OC    (user 8)
    pos 5:  N2  N2  N2  OFF OFF N1  N1    (user 11)
    pos 7:  N2  N2  N2  N2  N2  OFF OFF   (user 7)
    pos 10: OFF OFF OFF N1  N1  N1  N1    (user 10)

Today is pinned to Tue 20 Oct 2026.
"""

import datetime

import pytest

import app.database.database as db_module
from app.auth.auth import create_access_token
from app.core import swaps
from app.core.schedule import clear_schedule_cache
from app.database.database import RotationEra, ShiftOverride, ShiftSwap, SwapStatus, User, UserRole, WageType
from app.routes import schedule_personal, shift_swap
from tests.conftest import _ROTATION_ERA_PATTERN

D = datetime.date
TODAY = D(2026, 10, 20)
SLOT_TAKEN = "Ett byte finns redan för någon av personerna på dessa datum"


@pytest.fixture()
def client(test_db, test_client, monkeypatch):
    monkeypatch.setattr(db_module, "SessionLocal", lambda: test_db)
    for module in (swaps, shift_swap, schedule_personal):
        monkeypatch.setattr(module, "get_today", lambda: TODAY)
    clear_schedule_cache()
    test_db.add(
        RotationEra(start_date=D(2026, 1, 2), end_date=None, rotation_length=10, weeks_pattern=_ROTATION_ERA_PATTERN)
    )
    users = [(7, 7, UserRole.USER, 1), (8, 3, UserRole.USER, 1), (10, 10, UserRole.USER, 1), (11, 5, UserRole.USER, 1)]
    users += [(20, 1, UserRole.ADMIN, 1), (21, 2, UserRole.USER, 0)]  # never listed as colleagues
    for uid, pos, role, active in users:
        test_db.add(
            User(
                id=uid,
                username=f"u{uid}",
                password_hash="x",
                name=f"User {uid}",
                role=role,
                wage=30000,
                wage_type=WageType.MONTHLY,
                person_id=pos,
                vacation={},
                must_change_password=0,
                is_active=active,
            )
        )
    test_db.commit()
    yield test_client
    clear_schedule_cache()


def _login(client, user_id):
    client.cookies.set("access_token", create_access_token(data={"sub": str(user_id)}))


def _check(client, target_id, *pairs):
    resp = client.post(
        "/swaps/api/check",
        json={"target_id": target_id, "pairs": [{"req_date": str(r), "tgt_date": str(t)} for r, t in pairs]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


def test_check_classifies_kinds(client):
    _login(client, 7)
    cross, mutual = _check(client, 8, (D(2026, 10, 25), D(2026, 11, 1)), (D(2026, 10, 27), D(2026, 10, 27)))
    assert (cross["kind"], cross["blocked"]) == ("cross", None)
    assert (mutual["kind"], mutual["blocked"]) == ("mutual", None)
    # Same date with one side OFF is one-way in both directions.
    [give] = _check(client, 10, (D(2026, 10, 26), D(2026, 10, 26)))
    assert (give["kind"], give["blocked"]) == ("give_away", None)
    _login(client, 10)
    [take] = _check(client, 7, (D(2026, 10, 26), D(2026, 10, 26)))
    assert (take["kind"], take["blocked"]) == ("take_over", None)


def test_check_returns_blocked_reason(client, test_db):
    # Mirrors prod swap 22: user 8 already swapped 29 Oct with user 11 (legacy, swap_map).
    test_db.add(
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
    test_db.commit()
    _login(client, 8)
    taken, free = _check(client, 7, (D(2026, 10, 29), D(2026, 10, 29)), (D(2026, 10, 27), D(2026, 10, 27)))
    assert taken["blocked"] == SLOT_TAKEN
    assert taken["warnings"] == []
    assert free["blocked"] is None


def test_check_same_code_stays_blocked(client):
    _login(client, 7)
    [same] = _check(client, 11, (D(2026, 10, 27), D(2026, 10, 27)))
    assert same["blocked"] == "Ni jobbar redan samma pass"


def test_check_returns_warnings(client):
    _login(client, 8)
    [pair] = _check(client, 7, (D(2026, 10, 27), D(2026, 10, 27)))
    assert pair["blocked"] is None
    assert pair["warnings"] == ["Under 11 timmars dygnsvila (du)", "Under 11 timmars dygnsvila (kollegan)"]


def test_check_rejects_bad_requests(client):
    _login(client, 7)
    pair = {"req_date": "2026-10-25", "tgt_date": "2026-11-01"}
    assert client.post("/swaps/api/check", json={"target_id": 999, "pairs": [pair]}).status_code == 404
    assert client.post("/swaps/api/check", json={"target_id": 8, "pairs": [pair] * 11}).status_code == 422


def test_compare_clamps_window(client, test_db):
    test_db.add(ShiftOverride(user_id=7, date=D(2026, 10, 28), shift_code="OFF", created_by=7))
    test_db.commit()
    _login(client, 7)

    days = client.get("/swaps/api/compare", params={"target_id": 8, "start": "2026-10-01", "weeks": 10}).json()["days"]
    assert days[0]["date"] == "2026-10-20"
    assert len(days) == 42
    by_date = {d["date"]: d for d in days}
    tue = by_date["2026-10-27"]
    assert (tue["me"]["code"], tue["them"]["code"]) == ("N2", "N3")
    assert len(tue["me"]["start"]) == 5 and tue["me"]["start"][2] == ":"
    assert by_date["2026-10-29"]["them"] == {"code": "OFF", "start": None, "end": None}
    # Effective days: the override on 28 Oct frees user 7.
    assert by_date["2026-10-28"]["me"]["code"] == "OFF"

    default = client.get("/swaps/api/compare", params={"target_id": 8, "weeks": 0}).json()["days"]
    assert default[0]["date"] == "2026-10-20"
    assert len(default) == 7


def test_day_takers_lists_colleagues(client):
    _login(client, 7)
    resp = client.get("/swaps/api/day-takers", params={"date": "2026-10-27"})
    assert resp.status_code == 200
    by_id = {c["id"]: c for c in resp.json()["colleagues"]}
    assert set(by_id) == {8, 10, 11}
    assert by_id[8]["name"] == "User 8"
    assert (by_id[8]["kind"], by_id[8]["blocked"]) == ("mutual", None)
    assert by_id[8]["warnings"]
    assert (by_id[10]["kind"], by_id[10]["blocked"]) == ("give_away", None)
    assert by_id[10]["warnings"] == ["Under 11 timmars dygnsvila (kollegan)"]
    assert by_id[11]["blocked"] == "Ni jobbar redan samma pass"


def test_day_page_links_to_new(client):
    _login(client, 7)
    page = client.get("/day/7/2026/10/27").text
    assert 'href="/swaps/new?date=2026-10-27"' in page
    assert "Byt pass" in page
    assert 'id="swap-form"' not in page
    # Today, past days and other people's days get no link (an admin sees user 7's page).
    assert "/swaps/new" not in client.get("/day/7/2026/10/20").text
    assert "/swaps/new" not in client.get("/day/7/2026/10/19").text
    _login(client, 20)
    other = client.get("/day/7/2026/10/27")
    assert other.status_code == 200
    assert "/swaps/new" not in other.text


def test_old_picker_endpoint_is_gone(client):
    _login(client, 7)
    assert client.get("/swaps/api/shifts/8").status_code in (404, 405)


def test_check_blocks_ineligible_participants(client):
    pair = (D(2026, 10, 27), D(2026, 11, 3))
    _login(client, 7)
    for target in (20, 21):
        assert _check(client, target, pair)[0]["blocked"] == "Användaren kan inte byta pass"
    _login(client, 20)
    assert _check(client, 7, pair)[0]["blocked"] == "Du kan inte byta pass"


def test_compare_rejects_ineligible_target(client):
    _login(client, 7)
    for target in (20, 21):
        assert client.get("/swaps/api/compare", params={"target_id": target}).status_code == 404


def test_compare_rejects_far_start(client):
    _login(client, 7)
    for start in ("9999-12-31", "2028-01-01"):
        resp = client.get("/swaps/api/compare", params={"target_id": 8, "start": start})
        assert resp.status_code == 400
        assert "Ogiltigt startdatum" in resp.text
    assert client.get("/swaps/api/compare", params={"target_id": 8, "start": "2027-10-01"}).status_code == 200


def test_propose_rejects_ineligible_and_long_message(client):
    _login(client, 7)
    form = {"req_date": "2026-10-27", "tgt_date": "2026-11-03"}
    resp = client.post("/swaps/propose", data={**form, "target_id": 20}, follow_redirects=False)
    assert resp.status_code == 400
    assert "Användaren kan inte byta pass" in resp.text
    resp = client.post("/swaps/propose", data={**form, "target_id": 8, "message": "x" * 256}, follow_redirects=False)
    assert resp.status_code == 400
    assert "Meddelandet är för långt (högst 255 tecken)" in resp.text
    resp = client.post("/swaps/propose", data={**form, "target_id": 8, "message": "x" * 255}, follow_redirects=False)
    assert "för långt" not in resp.text
