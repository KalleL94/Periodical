"""Regression tests: absence deductions must price shift times using the
position the user held on the ABSENCE DATE, not their current position.

Reproduces the bug documented in PR #276's known-follow-ups: after a mid-month
rotation swap, get_shift_times_for_date resolved the shift for a past absence
date via user.rotation_person_id -- the current denormalized position -- instead
of the position actually held on that date (PersonHistory). A sick day recorded
before the swap was therefore priced with the successor's shift times.
"""

import datetime

import pytest

from app.core.schedule.person_history import start_employment, swap_positions
from app.core.schedule.wages import (
    get_absence_deductions_for_month,
    get_absent_hours_for_absence,
    get_shift_times_for_date,
)
from app.database.database import Absence, AbsenceType, User, UserRole, WageType

ERA_START = datetime.date(2026, 1, 2)
SWAP_DATE = datetime.date(2026, 1, 20)
ABSENCE_DATE = datetime.date(2026, 1, 7)  # before the swap


def _make_user(session, uid, username, name):
    user = User(
        id=uid,
        username=username,
        password_hash="x",
        name=name,
        role=UserRole.USER,
        wage=17333,
        wage_type=WageType.MONTHLY,
        vacation={},
        must_change_password=0,
        is_active=0,
    )
    session.add(user)
    session.commit()
    return user


def _set_up_swap(session):
    """Two holders start at positions 1 and 3, then swap positions mid-month.

    Returns (holder_a, holder_b). holder_a held position 1 (shift N3 on
    ABSENCE_DATE, 22:00 -> 06:30 next day) before the swap, and position 3
    (shift N2, 14:00 -> 22:30) after it.
    """
    holder_a = _make_user(session, 10, "holder_a", "Holder A")
    holder_b = _make_user(session, 11, "holder_b", "Holder B")

    start_employment(session, holder_a.id, 1, holder_a.name, holder_a.username, ERA_START, created_by=1)
    start_employment(session, holder_b.id, 3, holder_b.name, holder_b.username, ERA_START, created_by=1)

    swap_positions(session, 1, 3, SWAP_DATE, created_by=1)
    session.refresh(holder_a)
    # Sanity check: the swap did move holder_a's CURRENT position to 3.
    assert holder_a.person_id == 3

    return holder_a, holder_b


class TestShiftTimesFollowPositionHeldOnDate:
    def test_pre_swap_absence_uses_old_position_shift(self, rotation_session):
        session = rotation_session
        holder_a, _ = _set_up_swap(session)

        hours, start_dt, end_dt = get_shift_times_for_date(session, holder_a.id, ABSENCE_DATE)

        # Position 1's shift on 2026-01-07 is N3 (22:00 -> 06:30 next day), NOT
        # position 3's N2 (14:00 -> 22:30), which is holder_a's shift AFTER the swap.
        assert start_dt == datetime.datetime(2026, 1, 7, 22, 0)
        assert end_dt == datetime.datetime(2026, 1, 8, 6, 30)
        assert hours == pytest.approx(8.5)

    def test_post_swap_absence_uses_new_position_shift(self, rotation_session):
        """Control case: an absence ON/AFTER the swap date must use the new position.

        Position 1's shift on the swap date is OFF (fallback: 8.5h, no start/end).
        Position 3's shift on the swap date is N3 (22:00 -> 06:30 next day). Getting
        the real N3 times back (not the OFF fallback) confirms the swap date itself
        already resolves to the new position, as it should.
        """
        session = rotation_session
        holder_a, _ = _set_up_swap(session)

        hours, start_dt, end_dt = get_shift_times_for_date(session, holder_a.id, SWAP_DATE)

        assert start_dt == datetime.datetime(2026, 1, 20, 22, 0)
        assert end_dt == datetime.datetime(2026, 1, 21, 6, 30)
        assert hours == pytest.approx(8.5)

    def test_pre_swap_partial_absence_hours_use_old_position_times(self, rotation_session):
        """A left_at partial-day absence must be computed against the pre-swap
        shift window, not the current (post-swap) one."""
        session = rotation_session
        holder_a, _ = _set_up_swap(session)

        absence = Absence(user_id=holder_a.id, date=ABSENCE_DATE, absence_type=AbsenceType.SICK, left_at="02:00")

        shift_hours, shift_start_dt, shift_end_dt = get_shift_times_for_date(session, holder_a.id, ABSENCE_DATE)
        absent_hours = get_absent_hours_for_absence(absence, shift_start_dt, shift_end_dt, shift_hours)

        # Left at 02:00 during the old position's N3 night shift (22:00 -> 06:30):
        # 4.5h remained unworked. The bug (resolving position 3's N2 shift,
        # 14:00 -> 22:30) would instead cap this at a full 8.5h day.
        assert absent_hours == pytest.approx(4.5)

    def test_pre_swap_month_deduction_reflects_old_position_hours(self, rotation_session):
        """End-to-end: get_absence_deductions_for_month must total the
        pre-swap partial-day hours, not the post-swap full-day hours."""
        session = rotation_session
        holder_a, _ = _set_up_swap(session)

        session.add(Absence(user_id=holder_a.id, date=ABSENCE_DATE, absence_type=AbsenceType.SICK, left_at="02:00"))
        session.commit()

        result = get_absence_deductions_for_month(session, holder_a.id, 2026, 1, holder_a.wage)

        assert result["sick_hours"] == pytest.approx(4.5)
        assert result["total_hours"] == pytest.approx(4.5)


class TestLegacyFallbackWithoutHistory:
    """Positions without any PersonHistory rows must keep resolving via the
    current rotation_person_id snapshot exactly as before this fix."""

    def test_no_history_falls_back_to_rotation_person_id(self, rotation_session):
        session = rotation_session
        # rotation_session already seeds a User(id=1, person_id=1) with no
        # PersonHistory rows at all.
        hours, start_dt, end_dt = get_shift_times_for_date(session, 1, ABSENCE_DATE)

        assert start_dt == datetime.datetime(2026, 1, 7, 22, 0)
        assert end_dt == datetime.datetime(2026, 1, 8, 6, 30)
        assert hours == pytest.approx(8.5)
