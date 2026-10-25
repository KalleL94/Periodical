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
from app.core.schedule import calculate_shift_hours, clear_schedule_cache
from app.core.swaps import effective_code
from app.database.database import (
    OnCallOverride,
    OnCallOverrideType,
    PersonHistory,
    RotationEra,
    ShiftOverride,
    ShiftSwap,
    SwapStatus,
    User,
    UserRole,
    WageType,
)
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


def test_check_kinds_follow_effective_codes(client, test_db):
    # User 8 has OC on Sat 31 Oct, user 7 is free: OC is given away like any shift.
    _login(client, 8)
    [give] = _check(client, 7, (D(2026, 10, 31), D(2026, 10, 31)))
    assert (give["kind"], give["blocked"]) == ("give_away", None)
    # An on-call ADD makes user 10's free 27 Oct an OC day, swapped against N2.
    test_db.add(OnCallOverride(user_id=10, date=D(2026, 10, 27), override_type=OnCallOverrideType.ADD))
    test_db.commit()
    _login(client, 10)
    [mutual] = _check(client, 7, (D(2026, 10, 27), D(2026, 10, 27)))
    assert (mutual["kind"], mutual["blocked"]) == ("mutual", None)


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


def test_check_speaks_the_users_language(client, test_db):
    for uid in (7, 8):
        test_db.get(User, uid).language = "en"
    test_db.commit()
    _login(client, 8)
    [pair] = _check(client, 7, (D(2026, 10, 27), D(2026, 10, 27)))
    assert pair["warnings"] == [
        "Less than 11 hours of daily rest (you)",
        "Less than 11 hours of daily rest (colleague)",
    ]
    _login(client, 7)
    [same] = _check(client, 11, (D(2026, 10, 27), D(2026, 10, 27)))
    assert same["blocked"] == "You already work the same shift"
    # The Swedish colleague asking the reverse question still reads Swedish.
    _login(client, 11)
    [same] = _check(client, 7, (D(2026, 10, 27), D(2026, 10, 27)))
    assert same["blocked"] == "Ni jobbar redan samma pass"


def test_check_rejects_bad_requests(client):
    _login(client, 7)
    pair = {"req_date": "2026-10-25", "tgt_date": "2026-11-01"}
    assert client.post("/swaps/api/check", json={"target_id": 999, "pairs": [pair]}).status_code == 404
    assert client.post("/swaps/api/check", json={"target_id": 8, "pairs": [pair] * 11}).status_code == 422


def test_compare_clamps_window(client, test_db):
    test_db.add(ShiftOverride(user_id=7, date=D(2026, 10, 28), shift_code="OFF", created_by=7))
    test_db.commit()
    _login(client, 7)

    # A start before this week's Monday is clamped to that Monday, weeks capped at 6.
    days = client.get("/swaps/api/compare", params={"target_id": 8, "start": "2026-10-01", "weeks": 10}).json()["days"]
    assert days[0]["date"] == "2026-10-19"
    assert days[-1]["date"] == "2026-11-29"
    assert len(days) == 42
    # Today and earlier are past.
    assert [d.get("past", False) for d in days[:3]] == [True, True, False]
    by_date = {d["date"]: d for d in days}
    tue = by_date["2026-10-27"]
    assert (tue["me"]["code"], tue["them"]["code"]) == ("N2", "N3")
    assert len(tue["me"]["start"]) == 5 and tue["me"]["start"][2] == ":"
    assert by_date["2026-10-29"]["them"] == {"code": "OFF", "start": None, "end": None}
    # Effective days: the override on 28 Oct frees user 7.
    assert by_date["2026-10-28"]["me"]["code"] == "OFF"

    one = client.get("/swaps/api/compare", params={"target_id": 8, "weeks": 0}).json()["days"]
    assert (one[0]["date"], len(one)) == ("2026-10-19", 7)

    # Default: five whole Mon to Sun weeks.
    default = client.get("/swaps/api/compare", params={"target_id": 8}).json()["days"]
    assert (default[0]["date"], default[-1]["date"], len(default)) == ("2026-10-19", "2026-11-22", 35)

    # A mid-week start snaps back to its Monday, no past days in the future.
    later = client.get("/swaps/api/compare", params={"target_id": 8, "start": "2026-11-26"}).json()["days"]
    assert (later[0]["date"], later[-1]["date"]) == ("2026-11-23", "2026-12-27")
    assert not any(d.get("past") for d in later)


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


def _n2_then_n1(test_db):
    # User 7: N2 on Mon 26 and, by override, N1 on Tue 27. User 10 is free both days.
    test_db.add(ShiftOverride(user_id=7, date=D(2026, 10, 27), shift_code="N1", created_by=7))
    test_db.commit()
    clear_schedule_cache()
    return [(D(2026, 10, 26),) * 2, (D(2026, 10, 27),) * 2]


def test_check_warns_over_the_whole_list(client, test_db):
    pairs = _n2_then_n1(test_db)
    _login(client, 10)
    daily = "Under 11 timmars dygnsvila (du)"
    assert daily not in _check(client, 7, pairs[0])[0]["warnings"]
    assert daily not in _check(client, 7, pairs[1])[0]["warnings"]
    assert all(daily in r["warnings"] for r in _check(client, 7, *pairs))


def test_inbox_card_shows_the_combined_warning(client, test_db):
    pairs = _n2_then_n1(test_db)
    for rd, td in pairs:
        test_db.add(
            ShiftSwap(
                requester_id=10,
                target_id=7,
                requester_date=rd,
                target_date=td,
                requester_shift_code="OFF",
                target_shift_code=effective_code(test_db, test_db.get(User, 7), td),
                status=SwapStatus.PENDING,
                group_id="g1",
            )
        )
    test_db.commit()
    _login(client, 7)
    assert client.get("/swaps/").text.count("Under 11 timmars dygnsvila (du)") == 2


def test_compare_shows_the_on_call_window(client, test_db):
    # User 10 is free on 27 and 28 Oct: a windowed ADD on 27th, a whole-day ADD on 28th.
    test_db.add(
        OnCallOverride(
            user_id=10,
            date=D(2026, 10, 27),
            override_type=OnCallOverrideType.ADD,
            start_time="00:00",
            end_time="12:00",
        )
    )
    test_db.add(OnCallOverride(user_id=10, date=D(2026, 10, 28), override_type=OnCallOverrideType.ADD))
    test_db.commit()
    _login(client, 7)
    days = client.get("/swaps/api/compare", params={"target_id": 10}).json()["days"]
    by_date = {d["date"]: d["them"] for d in days}
    assert by_date["2026-10-27"] == {"code": "OC", "start": "00:00", "end": "12:00"}
    _, start_dt, end_dt = calculate_shift_hours(D(2026, 10, 28), "OC")
    plain = by_date["2026-10-28"]
    assert plain == {"code": "OC", "start": start_dt.strftime("%H:%M"), "end": end_dt.strftime("%H:%M")}


@pytest.mark.parametrize(
    ("lang", "message"), [("sv", "Datumet ligger för långt fram"), ("en", "The date is too far ahead")]
)
@pytest.mark.parametrize("far", ["9999-12-31", "2029-10-23"])
def test_far_future_dates_are_refused(client, test_db, lang, message, far):
    test_db.get(User, 7).language = lang
    test_db.commit()
    _login(client, 7)
    for pair in ({"req_date": far, "tgt_date": "2026-10-27"}, {"req_date": "2026-10-27", "tgt_date": far}):
        resp = client.post("/swaps/api/check", json={"target_id": 8, "pairs": [pair]})
        assert resp.status_code == 400
        assert message in resp.text
        resp = client.post("/swaps/propose", data={"target_id": 8, **pair}, follow_redirects=False)
        assert resp.status_code == 400
        assert message in resp.text
    resp = client.get("/swaps/api/day-takers", params={"date": far})
    assert resp.status_code == 400
    assert message in resp.text
    assert not test_db.query(ShiftSwap).count()


@pytest.mark.parametrize(
    ("lang", "message"), [("sv", "Kan bara byta framtida pass"), ("en", "Only future shifts can be swapped")]
)
@pytest.mark.parametrize("past", ["0001-01-01", "2026-10-19"])
def test_past_dates_are_refused_before_reading_schedules(client, test_db, monkeypatch, lang, message, past):
    def boom(*args, **kwargs):
        raise AssertionError("schedule read for a past date")

    monkeypatch.setattr(swaps, "build_week_data", boom)
    test_db.get(User, 7).language = lang
    test_db.commit()
    _login(client, 7)
    for pair in ({"req_date": past, "tgt_date": "2026-10-27"}, {"req_date": "2026-10-27", "tgt_date": past}):
        resp = client.post("/swaps/api/check", json={"target_id": 8, "pairs": [pair]})
        assert resp.status_code == 400
        assert message in resp.text
        resp = client.post("/swaps/propose", data={"target_id": 8, **pair}, follow_redirects=False)
        assert resp.status_code == 400
        assert message in resp.text
    resp = client.get("/swaps/api/day-takers", params={"date": past})
    assert resp.status_code == 400
    assert message in resp.text
    assert not test_db.query(ShiftSwap).count()


def test_dates_outside_employment_are_refused_everywhere(client, test_db):
    # User 30 succeeds user 31 at pos 6 on Mon 2 Nov; before that pos 6 holds 31's shifts.
    for uid, active, start, end in ((31, 0, D(2026, 1, 2), D(2026, 11, 1)), (30, 1, D(2026, 11, 2), None)):
        test_db.add(
            User(
                id=uid,
                username=f"u{uid}",
                password_hash="x",
                name=f"User {uid}",
                role=UserRole.USER,
                wage=30000,
                wage_type=WageType.MONTHLY,
                person_id=6,
                vacation={},
                must_change_password=0,
                is_active=active,
            )
        )
        test_db.add(
            PersonHistory(
                user_id=uid,
                person_id=6,
                name=f"User {uid}",
                username=f"u{uid}",
                is_active=1,
                effective_from=start,
                effective_to=end,
            )
        )
    # A proposal written before the hire date was known, now awaiting 30.
    test_db.add(
        ShiftSwap(
            requester_id=7,
            target_id=30,
            requester_date=D(2026, 10, 28),
            target_date=D(2026, 10, 28),
            requester_shift_code="N2",
            target_shift_code="OFF",
            status=SwapStatus.PENDING,
        )
    )
    test_db.commit()
    refused = "Någon av personerna är inte anställd ett av datumen"
    _login(client, 7)
    [pair] = _check(client, 30, (D(2026, 10, 26), D(2026, 10, 26)))
    assert pair["blocked"] == refused
    resp = client.post(
        "/swaps/propose",
        data={"target_id": 30, "req_date": "2026-10-27", "tgt_date": "2026-10-27"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert refused in resp.text
    # The compare screen shows 30 free before the start, not 31's shifts.
    days = client.get("/swaps/api/compare", params={"target_id": 30, "start": "2026-10-26", "weeks": 1}).json()
    assert {d["them"]["code"] for d in days["days"]} == {"OFF"}
    _login(client, 30)
    swap = test_db.query(ShiftSwap).one()
    resp = client.post(f"/swaps/{swap.id}/accept", follow_redirects=False)
    assert resp.status_code == 400
    assert refused in resp.text
    test_db.refresh(swap)
    assert swap.status == SwapStatus.PENDING
