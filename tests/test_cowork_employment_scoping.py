"""Employment-window scoping for the cowork/handover stat builders.

`/cowork/<id>` statistics are built from a position's whole-year schedule. When a
position changes hands mid-year, a departed holder's page must not include the
successor's coworker interactions or handovers. These tests reproduce that leak
and lock in the fix: the builders accept the viewed user's session and user id
and mask days outside that user's own PersonHistory segment(s) for the position.

The cowork builders read the rotation era through the global SessionLocal (not an
injected session), so we reuse the rotation_session fixture, which monkeypatches
SessionLocal onto an in-memory engine with a seeded rotation era. PersonHistory
rows are seeded on that same session.
"""

import datetime

from app.core.schedule.cowork import (
    build_cowork_details,
    build_cowork_stats,
    build_handover_details,
)
from app.core.schedule.person_history import add_person_change, start_employment
from app.database.database import User, UserRole, WageType

# Anna held position 3 from rotation start; Bert took over on 2026-02-01, so
# add_person_change closes Anna on 2026-01-31.
POSITION = 3
ANNA_ID = 11
BERT_ID = 12
ANNA_START = datetime.date(2026, 1, 2)
ANNA_END = datetime.date(2026, 1, 31)
BERT_START = datetime.date(2026, 2, 1)
YEAR = 2026


def _make_user(session, uid, username, name):
    user = User(
        id=uid,
        username=username,
        password_hash="x",
        name=name,
        role=UserRole.USER,
        wage=30000,
        wage_type=WageType.MONTHLY,
        vacation={},
        must_change_password=0,
        is_active=0,
    )
    session.add(user)
    session.commit()
    return user


def _seed_succession(session):
    """Anna holds position 3 until 2026-01-31; Bert succeeds her on 2026-02-01."""
    anna = _make_user(session, ANNA_ID, "anna1", "Anna")
    bert = _make_user(session, BERT_ID, "bert1", "Bert")
    start_employment(session, anna.id, POSITION, "Anna", "anna1", ANNA_START, created_by=1)
    add_person_change(
        session,
        old_user_id=anna.id,
        new_user_id=bert.id,
        person_id=POSITION,
        new_name="Bert",
        new_username="bert1",
        effective_from=BERT_START,
        created_by=1,
    )
    return anna, bert


def _all_cowork_dates(session, employment_user_id):
    """Aggregate cowork detail dates across every other position."""
    dates = []
    for other in range(1, 11):
        if other == POSITION:
            continue
        kwargs = {}
        if employment_user_id is not None:
            kwargs = {"session": session, "employment_user_id": employment_user_id}
        dates += [r["date"] for r in build_cowork_details(YEAR, POSITION, other, **kwargs)]
    return dates


def _all_handover_dates(session, employment_user_id):
    """Aggregate handover detail dates across every other position."""
    dates = []
    for other in range(1, 11):
        if other == POSITION:
            continue
        kwargs = {}
        if employment_user_id is not None:
            kwargs = {"session": session, "employment_user_id": employment_user_id}
        dates += [r["date"] for r in build_handover_details(YEAR, POSITION, other, **kwargs)]
    return dates


def test_cowork_details_leak_without_scoping_and_fixed_with_scoping(rotation_session):
    """Departed Anna's cowork details must stay inside her tenure once scoped."""
    session = rotation_session
    anna, _bert = _seed_succession(session)

    unscoped = _all_cowork_dates(session, employment_user_id=None)
    scoped = _all_cowork_dates(session, employment_user_id=anna.id)

    # The unscoped builder leaks the successor's post-departure coworking.
    assert any(d > ANNA_END for d in unscoped), "expected the unscoped view to include post-departure days"
    # Scoped to Anna's tenure, no day falls outside her employment window.
    assert scoped, "Anna should still have coworking days within her tenure"
    assert all(ANNA_START <= d <= ANNA_END for d in scoped)
    # Scoping strictly removes the leaked days.
    assert len(scoped) < len(unscoped)


def test_handover_details_leak_without_scoping_and_fixed_with_scoping(rotation_session):
    """Departed Anna's handover details must stay inside her tenure once scoped."""
    session = rotation_session
    anna, _bert = _seed_succession(session)

    unscoped = _all_handover_dates(session, employment_user_id=None)
    scoped = _all_handover_dates(session, employment_user_id=anna.id)

    assert any(d > ANNA_END for d in unscoped), "expected the unscoped view to include post-departure handovers"
    assert scoped, "Anna should still have handovers within her tenure"
    assert all(ANNA_START <= d <= ANNA_END for d in scoped)
    assert len(scoped) < len(unscoped)


def test_cowork_stats_totals_scoped_to_tenure(rotation_session):
    """Aggregate cowork and handover counts drop to Anna's own tenure."""
    session = rotation_session
    anna, _bert = _seed_succession(session)

    unscoped = build_cowork_stats(YEAR, POSITION)
    scoped = build_cowork_stats(YEAR, POSITION, session=session, employment_user_id=anna.id)

    unscoped_cowork = sum(r["total"] for r in unscoped)
    scoped_cowork = sum(r["total"] for r in scoped)
    unscoped_handovers = sum(r["handovers"] for r in unscoped)
    scoped_handovers = sum(r["handovers"] for r in scoped)

    # Anna held the position for only January, so her scoped counts must be a
    # strict subset of the full-year totals the unscoped builder produced.
    assert scoped_cowork > 0
    assert scoped_cowork < unscoped_cowork
    assert scoped_handovers > 0
    assert scoped_handovers < unscoped_handovers


def test_position_without_history_is_unaffected(rotation_session):
    """A position with no PersonHistory keeps the legacy whole-year behavior.

    Position 1 has a User but no PersonHistory rows, so passing a session and
    employment_user_id must not mask anything (legacy fallback), matching the
    plain call exactly.
    """
    session = rotation_session

    plain = build_cowork_stats(YEAR, 1)
    with_args = build_cowork_stats(YEAR, 1, session=session, employment_user_id=1)

    assert [r["total"] for r in plain] == [r["total"] for r in with_args]
    assert [r["handovers"] for r in plain] == [r["handovers"] for r in with_args]

    plain_details = build_cowork_details(YEAR, 1, 2)
    scoped_details = build_cowork_details(YEAR, 1, 2, session=session, employment_user_id=1)
    assert [r["date"] for r in plain_details] == [r["date"] for r in scoped_details]
