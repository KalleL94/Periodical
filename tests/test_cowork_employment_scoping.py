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

from app.core.schedule.core import clear_schedule_cache
from app.core.schedule.cowork import (
    build_cowork_details,
    build_cowork_stats,
    build_handover_details,
)
from app.core.schedule.person_history import (
    add_person_change,
    end_employment,
    start_employment,
    swap_positions,
)
from app.database.database import Absence, AbsenceType, User, UserRole, WageType

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


# ── Position changes (the viewed user moves, the position stays) ──────────
# swap_positions moves a user to another rotation position. The cowork builders
# are called with the position the route resolved for *today*, which is not the
# position the user holds in the year being viewed. Scoping must follow the user
# across positions instead of masking every day of a position they no longer hold.

OTHER_POSITION = 8
CARL_ID = 13
SWAP_DATE = datetime.date(2026, 7, 1)


def _seed_swap(session):
    """Anna (pos 3) and Carl (pos 8) trade positions on 2026-07-01."""
    anna = _make_user(session, ANNA_ID, "anna1", "Anna")
    carl = _make_user(session, CARL_ID, "carl1", "Carl")
    start_employment(session, anna.id, POSITION, "Anna", "anna1", ANNA_START, created_by=1)
    start_employment(session, carl.id, OTHER_POSITION, "Carl", "carl1", ANNA_START, created_by=1)
    swap_positions(session, POSITION, OTHER_POSITION, SWAP_DATE, created_by=1)
    return anna, carl


def test_cowork_stats_follow_the_user_across_a_position_change(rotation_session):
    """A year after the swap must not come back empty just because the route
    resolved the pre-swap position."""
    session = rotation_session
    anna, _carl = _seed_swap(session)

    # 2027: Anna sits at position 8 all year, but the route resolves position 3
    # (the one she holds "today", before the swap date in the test clock).
    rows = build_cowork_stats(2027, POSITION, session=session, employment_user_id=anna.id)

    assert sum(r["total"] for r in rows) > 0, "a full year at the new position must not report zero coworking"
    assert sum(r["handovers"] for r in rows) > 0


def test_the_swapped_colleague_is_one_row_spanning_both_positions(rotation_session):
    """Carl traded positions with Anna, so he is her coworker all year.

    Rows are keyed by person, not position: keying by position dropped Carl
    entirely, because Anna occupied both of the positions he sat at.
    """
    session = rotation_session
    anna, carl = _seed_swap(session)

    rows = build_cowork_stats(YEAR, POSITION, session=session, employment_user_id=anna.id)
    carl_rows = [r for r in rows if r["other_user_id"] == carl.id]

    assert len(carl_rows) == 1, "Carl must appear once, not once per position he held"
    assert carl_rows[0]["other_name"] == "Carl"
    assert carl_rows[0]["total"] > 0
    # Filed under the position he holds at year end, which is Anna's old one.
    assert carl_rows[0]["other_id"] == POSITION

    # Anna is never her own coworker, at either position she held.
    assert not [r for r in rows if r["other_user_id"] == anna.id]

    # Her shared days with Carl cover both halves of the year: she sat at
    # position 3 before the swap and at position 8 after it.
    details = build_cowork_details(
        YEAR, POSITION, POSITION, session=session, employment_user_id=anna.id, other_user_id=carl.id
    )
    assert any(d["date"] < SWAP_DATE for d in details)
    assert any(d["date"] >= SWAP_DATE for d in details)


def test_handover_details_follow_the_user_across_a_position_change(rotation_session):
    session = rotation_session
    anna, _carl = _seed_swap(session)

    rows = build_handover_details(2027, POSITION, 5, session=session, employment_user_id=anna.id)

    assert rows, "handovers at the new position must still be reported"


def test_a_successor_is_not_a_coworker(rotation_session):
    """Bert only ever held Anna's position after she left, so they never met.

    Rows are keyed by person, which makes the successor a candidate row. He must
    be dropped: a colleague is someone who held a position on a day the viewed
    person did too.
    """
    session = rotation_session
    anna, bert = _seed_succession(session)

    rows = build_cowork_stats(YEAR, POSITION, session=session, employment_user_id=anna.id)

    assert not [r for r in rows if r["other_user_id"] == bert.id]
    assert not [r for r in rows if r["other_user_id"] == anna.id]


def test_rows_are_alphabetical_with_departed_people_last(rotation_session):
    """Anna's own list: names A-Z, and anyone whose record is closed at the end."""
    session = rotation_session
    anna, carl = _seed_swap(session)
    # Close Carl's record so he counts as departed while still having worked with Anna.
    end_employment(session, carl.id, POSITION, datetime.date(2026, 11, 30))

    rows = build_cowork_stats(YEAR, POSITION, session=session, employment_user_id=anna.id)
    names = [r["other_name"] for r in rows]

    assert names == sorted(n for n in names if n != "Carl") + ["Carl"]
    assert rows[-1]["active"] is False
    assert all(r["active"] for r in rows[:-1])


def test_a_sick_day_is_not_a_shared_shift(rotation_session):
    """Absences only reach the builders through the session.

    Without one, generate_year_data hands back the raw rotation, so a day the
    coworker called in sick still counted as a shift worked together.
    """
    session = rotation_session
    anna, carl = _seed_swap(session)

    kwargs = {"session": session, "employment_user_id": anna.id, "other_user_id": carl.id}
    before = build_cowork_details(YEAR, POSITION, POSITION, **kwargs)
    assert before, "Anna and Carl need shared days for this test to mean anything"

    session.add(Absence(user_id=carl.id, date=before[0]["date"], absence_type=AbsenceType.SICK))
    session.commit()
    clear_schedule_cache()

    after = build_cowork_details(YEAR, POSITION, POSITION, **kwargs)
    assert [d["date"] for d in after] == [d["date"] for d in before[1:]]
