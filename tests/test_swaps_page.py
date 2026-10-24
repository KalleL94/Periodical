"""The /swaps inbox shows one card per proposal, with every pair and the group buttons."""

import datetime

from app.auth.auth import create_access_token
from app.database.database import ShiftSwap, SwapStatus, User, UserRole, WageType
from app.routes import shift_swap

D = datetime.date


def _user(db, uid, name):
    db.add(
        User(
            id=uid,
            username=f"u{uid}",
            password_hash="x",
            name=name,
            role=UserRole.USER,
            wage=30000,
            wage_type=WageType.MONTHLY,
            person_id=uid,
            vacation={},
            must_change_password=0,
        )
    )


def _row(db, group_id, req_date, tgt_date, req_code, tgt_code, status=SwapStatus.PENDING):
    row = ShiftSwap(
        requester_id=1,
        target_id=2,
        requester_date=req_date,
        target_date=tgt_date,
        requester_shift_code=req_code,
        target_shift_code=tgt_code,
        status=status,
        group_id=group_id,
        message="Hej Anna" if group_id else None,
    )
    db.add(row)
    db.commit()
    return row


def _setup(test_db, monkeypatch):
    _user(test_db, 1, "Me Myself")
    _user(test_db, 2, "Anna Colleague")
    test_db.commit()
    calls = []

    def fake_warnings(db, requester, target, pairs, cache=None):
        calls.append(pairs)
        return [["Under 11 timmars dygnsvila (du)"]] * len(pairs)

    monkeypatch.setattr(shift_swap, "proposal_warnings", fake_warnings)
    first = _row(test_db, "g1", D(2026, 10, 27), D(2026, 10, 28), "D1", "N1")
    _row(test_db, "g1", D(2026, 10, 29), D(2026, 10, 29), "OFF", "D2")
    legacy = _row(test_db, None, D(2026, 11, 3), D(2026, 11, 3), "D1", "OFF", SwapStatus.ACCEPTED)
    return first, legacy, calls


def test_inbox_groups_rows(test_client, test_db, monkeypatch):
    first, legacy, calls = _setup(test_db, monkeypatch)

    test_client.cookies.set("access_token", create_access_token(data={"sub": "1"}))
    sent = test_client.get("/swaps/")
    assert sent.status_code == 200
    page = sent.text
    # Two proposals sent: one card each, the two-pair group shows both pairs.
    assert page.count('class="swap-card"') == 2
    assert page.count('class="swap-card-pair"') == 3
    assert "Hej Anna" in page
    # The pending group's warnings come from one call over both pairs together.
    assert calls == [[(D(2026, 10, 27), D(2026, 10, 28)), (D(2026, 10, 29), D(2026, 10, 29))]]
    assert "Under 11 timmars dygnsvila (du)" in page
    # Pending group: cancel posts to the first row. Accepted legacy: cancel too.
    assert f'action="/swaps/{first.id}/cancel"' in page
    assert f'action="/swaps/{legacy.id}/cancel"' in page
    assert "/accept" not in page
    # Entry point to the compare screen.
    assert 'href="/swaps/new"' in page

    test_client.cookies.set("access_token", create_access_token(data={"sub": "2"}))
    page = test_client.get("/swaps/").text
    assert page.count('class="swap-card"') == 2
    assert f'action="/swaps/{first.id}/accept"' in page
    assert f'action="/swaps/{first.id}/reject"' in page
    # Warnings only for pending groups: the accepted legacy card adds none.
    assert page.count("Under 11 timmars dygnsvila (du)") == 2
