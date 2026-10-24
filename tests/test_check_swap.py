"""Table-driven tests for app.core.swaps.check_swap.

Uses the rotation_session fixture (10-week test pattern, era from 2026-01-02).
Positions used, week of Mon 26 Oct 2026 (Sun 25 to Sat 31):

    pos 1: OC  OC  OFF N2  N2  N2  OFF
    pos 3: OFF N3  N3  N3  OFF OFF OC    (user 8, like Okan on dev)
    pos 5: N2  N2  N2  OFF OFF N1  N1
    pos 7: N2  N2  N2  N2  N2  OFF OFF   (user 7, like Marcus on dev)
    pos 9: N3  OFF OC  N3  N3  N3  N3    (user 12)
    pos 10: OFF OFF OFF N1 N1  N1  N1
"""

import datetime

import pytest

from app.core import swaps
from app.core.swaps import check_swap
from app.database.database import (
    Absence,
    AbsenceType,
    OnCallOverride,
    OnCallOverrideType,
    OvertimeShift,
    ShiftOverride,
    ShiftSwap,
    SwapStatus,
    User,
    UserRole,
    WageType,
)

D = datetime.date
SLOT_TAKEN = "Ett byte finns redan för någon av personerna på dessa datum"


@pytest.fixture
def db(rotation_session, monkeypatch):
    monkeypatch.setattr(swaps, "get_today", lambda: D(2026, 10, 20))
    for uid, pos in ((7, 7), (8, 3), (9, 1), (10, 10), (11, 5), (12, 9)):
        rotation_session.add(
            User(
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
        )
    rotation_session.commit()
    return rotation_session


def _swap(db, req, tgt, rd, td, status):
    db.add(
        ShiftSwap(
            requester_id=req,
            target_id=tgt,
            requester_date=rd,
            target_date=td,
            requester_shift_code="N3",
            target_shift_code="N2",
            status=status,
        )
    )


def _legacy_accepted(db):
    # Mirrors prod swap 22: user 8 already swapped 29 Oct with someone else.
    _swap(db, 8, 11, D(2026, 10, 29), D(2026, 10, 29), SwapStatus.ACCEPTED)


def _new_accepted(db):
    db.add(
        ShiftSwap(
            requester_id=9,
            target_id=7,
            requester_date=D(2026, 10, 29),
            target_date=D(2026, 10, 29),
            requester_shift_code="N2",
            target_shift_code="N2",
            status=SwapStatus.ACCEPTED,
            applied_as_override=True,
        )
    )


def _duplicate(db):
    _swap(db, 8, 7, D(2026, 10, 28), D(2026, 10, 29), SwapStatus.PENDING)


def _manual_override(db):
    db.add(ShiftOverride(user_id=7, date=D(2026, 10, 28), shift_code="N1"))


def _manual_n1(db):
    # User 7 has N2 in the rotation on 26 Oct; an admin changed it to N1.
    db.add(ShiftOverride(user_id=7, date=D(2026, 10, 26), shift_code="N1"))


def _oncall_add(db):
    # User 10 is OFF in the rotation on 27 Oct; an admin added on-call.
    db.add(OnCallOverride(user_id=10, date=D(2026, 10, 27), override_type=OnCallOverrideType.ADD))


def _etc(db):
    db.add(
        ShiftOverride(
            user_id=7,
            date=D(2026, 10, 27),
            shift_code="ETC",
            label="Kurs",
            start_time=datetime.time(8, 0),
            end_time=datetime.time(12, 0),
        )
    )


ETC_REFUSED = "Passet är ett eget pass och kan inte bytas"


def _absence(db):
    db.add(Absence(user_id=8, date=D(2026, 10, 29), absence_type=AbsenceType.SICK))


def _week_vacation(db):
    user = db.get(User, 7)
    user.vacation = {"2026": [44]}  # ISO week 44 = 26 Oct to 1 Nov


def _none(db):
    pass


CASES = [
    # id, setup, requester, target, req_date, tgt_date, expected
    ("same_person", _none, 8, 8, D(2026, 10, 28), D(2026, 10, 29), "Kan inte byta pass med dig själv"),
    ("past_date", _none, 8, 7, D(2026, 10, 20), D(2026, 10, 29), "Kan bara byta framtida pass"),
    ("duplicate", _duplicate, 8, 7, D(2026, 10, 28), D(2026, 10, 29), "Byte redan föreslaget för dessa datum"),
    ("legacy_swap_slot", _legacy_accepted, 8, 7, D(2026, 10, 28), D(2026, 10, 29), SLOT_TAKEN),
    ("new_swap_slot", _new_accepted, 8, 7, D(2026, 10, 28), D(2026, 10, 29), SLOT_TAKEN),
    # A manual change is no longer a stop: the effective N1 on the 28th means user 7 works then.
    ("manual_override", _manual_override, 8, 7, D(2026, 10, 28), D(2026, 10, 29), "Kollegan jobbar redan det datumet"),
    ("manual_override_allowed", _manual_n1, 7, 8, D(2026, 10, 26), D(2026, 10, 26), None),
    ("oncall_add_vs_n2", _oncall_add, 10, 7, D(2026, 10, 27), D(2026, 10, 27), None),
    ("etc_target", _etc, 8, 7, D(2026, 10, 27), D(2026, 10, 27), ETC_REFUSED),
    ("etc_requester", _etc, 7, 8, D(2026, 10, 27), D(2026, 10, 27), ETC_REFUSED),
    (
        "absence",
        _absence,
        8,
        7,
        D(2026, 10, 28),
        D(2026, 10, 29),
        "Någon av personerna är frånvarande ett av datumen",
    ),
    (
        "week_vacation",
        _week_vacation,
        8,
        7,
        D(2026, 10, 28),
        D(2026, 10, 29),
        "Någon av personerna har semester ett av datumen",
    ),
    ("oc_vs_n2_same_day", _none, 9, 7, D(2026, 10, 26), D(2026, 10, 26), None),
    ("oc_give_away", _none, 8, 7, D(2026, 10, 31), D(2026, 10, 31), None),
    ("oc_take_over", _none, 7, 8, D(2026, 10, 31), D(2026, 10, 31), None),
    ("oc_cross_for_n3", _none, 9, 8, D(2026, 10, 25), D(2026, 10, 27), None),
    # User 8 has OC on the 31st, so that day is not free.
    ("oc_not_free", _none, 8, 10, D(2026, 10, 26), D(2026, 10, 31), "Du jobbar redan det datumet"),
    # Was "OC-pass byts av admin"; now the ordinary cross-day rule decides.
    ("oc_cross_target_busy", _none, 9, 7, D(2026, 10, 26), D(2026, 10, 27), "Kollegan jobbar redan det datumet"),
    ("requester_off", _none, 8, 7, D(2026, 10, 29), D(2026, 10, 26), "Du jobbar inte det datumet"),
    ("target_off", _none, 8, 10, D(2026, 10, 28), D(2026, 10, 27), "Kollegan jobbar inte det datumet"),
    ("both_off_same_day", _none, 8, 10, D(2026, 10, 25), D(2026, 10, 25), "Ingen av er jobbar den dagen"),
    ("same_shift", _none, 7, 11, D(2026, 10, 26), D(2026, 10, 26), "Ni jobbar redan samma pass"),
    ("req_works_tgt_date", _none, 7, 8, D(2026, 10, 26), D(2026, 10, 27), "Du jobbar redan det datumet"),
    ("tgt_works_req_date", _none, 8, 7, D(2026, 10, 28), D(2026, 10, 29), "Kollegan jobbar redan det datumet"),
    ("cross_day_ok", _none, 7, 8, D(2026, 10, 25), D(2026, 11, 1), None),
    ("one_way_same_day", _none, 8, 10, D(2026, 10, 27), D(2026, 10, 27), None),
    ("mutual_same_day", _none, 8, 7, D(2026, 10, 27), D(2026, 10, 27), None),
]


@pytest.mark.parametrize(
    ("setup", "req_id", "tgt_id", "req_date", "tgt_date", "expected"),
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_check_swap_reasons(db, setup, req_id, tgt_id, req_date, tgt_date, expected):
    setup(db)
    db.commit()
    reason = check_swap(db, db.get(User, req_id), db.get(User, tgt_id), req_date, tgt_date)
    assert reason == expected


def test_effective_code_follows_overrides(db):
    _manual_n1(db)
    _oncall_add(db)
    db.commit()
    cache = {}
    assert swaps.effective_code(db, db.get(User, 7), D(2026, 10, 26), cache) == "N1"
    assert swaps.effective_code(db, db.get(User, 10), D(2026, 10, 27), cache) == "OC"
    assert swaps.effective_code(db, db.get(User, 10), D(2026, 10, 26), cache) == "OFF"
    # One build_week_data per user and ISO week.
    assert set(cache) == {(7, 2026, 44), (10, 2026, 44)}


@pytest.mark.parametrize(
    ("req_id", "tgt_id", "req_date", "tgt_date"),
    [
        (8, 10, D(2026, 10, 26), D(2026, 10, 29)),
        (7, 8, D(2026, 10, 29), D(2026, 11, 1)),
        (8, 10, D(2026, 10, 26), D(2026, 10, 24)),
        (7, 9, D(2026, 10, 27), D(2026, 10, 30)),
    ],
)
def test_check_swap_ignores_rest(db, req_id, tgt_id, req_date, tgt_date):
    # Rest breaches are warnings (proposal_warnings), no longer hard stops.
    assert check_swap(db, db.get(User, req_id), db.get(User, tgt_id), req_date, tgt_date) is None


def test_exclude_swap_ids_ignores_own_swap(db):
    # accept re-runs the rules on a PENDING swap; the swap must not block itself.
    _swap(db, 7, 8, D(2026, 10, 25), D(2026, 11, 1), SwapStatus.PENDING)
    db.commit()
    own = db.query(ShiftSwap).one()
    requester, target = db.get(User, 7), db.get(User, 8)
    assert check_swap(db, requester, target, D(2026, 10, 25), D(2026, 11, 1)) is not None
    assert (
        check_swap(db, requester, target, D(2026, 10, 25), D(2026, 11, 1), exclude_swap_ids=frozenset({own.id})) is None
    )


def test_called_in_overtime_cannot_be_swapped(db):
    # User 10 is OFF on 27 Oct; a called-in overtime row makes the day show "OT".
    db.add(
        OvertimeShift(
            user_id=10,
            date=D(2026, 10, 27),
            start_time=datetime.time(8, 0),
            end_time=datetime.time(16, 0),
            hours=8.0,
            ot_pay=0.0,
            kind="ot",
            side="full",
        )
    )
    db.commit()
    assert swaps.effective_code(db, db.get(User, 10), D(2026, 10, 27)) == "OT"
    refused = "Passet kan inte bytas"
    assert check_swap(db, db.get(User, 10), db.get(User, 8), D(2026, 10, 27), D(2026, 10, 27)) == refused
    assert check_swap(db, db.get(User, 8), db.get(User, 10), D(2026, 10, 27), D(2026, 10, 27)) == refused
