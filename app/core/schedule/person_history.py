# app/core/schedule/person_history.py
"""
Person history management for tracking employee changes over time.

This module handles employment periods, allowing:
- Multiple users to occupy the same person_id over time
- Historical data to show the correct person's name
- Old employees to view their historical data after leaving
- New employees to only see data from their start date
"""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.database.database import PersonHistory, User


def get_person_for_date(session: Session, person_id: int, effective_date: date) -> dict | None:
    """
    Get person data valid on a specific date.

    Args:
        session: Database session
        person_id: Position in rotation (1-10)
        effective_date: Date to check employment for

    Returns:
        Dict with: name, username, is_active, user_id
        Returns None if no person held this position on that date.

    Example:
        >>> person = get_person_for_date(db, person_id=6, effective_date=date(2026, 2, 15))
        >>> print(person["name"])  # "Kalle" (if Kalle held position 6 on 2026-02-15)
    """
    record = (
        session.query(PersonHistory)
        .filter(
            PersonHistory.person_id == person_id,
            PersonHistory.effective_from <= effective_date,
            (PersonHistory.effective_to.is_(None)) | (PersonHistory.effective_to >= effective_date),
        )
        .order_by(PersonHistory.effective_from.desc())
        .first()
    )

    if record:
        return {
            "name": record.name,
            "username": record.username,
            "is_active": record.is_active,
            "user_id": record.user_id,
        }

    # Fallback to User table if no PersonHistory exists
    user = session.query(User).filter(User.id == person_id).first()
    if user:
        return {
            "name": user.name,
            "username": user.username,
            "is_active": user.is_active,
            "user_id": user.id,
        }

    return None


def get_employment_period(session: Session, user_id: int, person_id: int) -> tuple[date, date | None]:
    """
    Get employment period for a specific user at a specific position.

    Args:
        session: Database session
        user_id: User ID
        person_id: Position in rotation (1-10)

    Returns:
        Tuple of (start_date, end_date) where end_date is None if currently employed.

    Example:
        >>> start, end = get_employment_period(db, user_id=6, person_id=6)
        >>> print(f"{start} to {end}")  # "2026-01-06 to 2026-03-31" or "2026-01-06 to None"
    """
    record = (
        session.query(PersonHistory)
        .filter(
            PersonHistory.user_id == user_id,
            PersonHistory.person_id == person_id,
        )
        .order_by(PersonHistory.effective_from.desc())
        .first()
    )

    if record:
        return (record.effective_from, record.effective_to)

    # Fallback: assume employed from rotation start
    from app.core.schedule.core import get_rotation_start_date

    return (get_rotation_start_date(), None)


def add_person_change(
    session: Session,
    old_user_id: int,
    new_user_id: int,
    person_id: int,
    new_name: str,
    new_username: str,
    effective_from: date,
    created_by: int,
    old_end_date: date | None = None,
) -> PersonHistory:
    """
    Register a person change (someone leaving, someone else starting).

    This function performs the whole swap in a single transaction:
    1. Closes old person's PersonHistory record (sets effective_to)
    2. Deactivates old user (sets is_active=0)
    3. Creates new person's PersonHistory record via the validated
       start_employment logic
    4. Activates new user (sets is_active=1)

    The close and deactivate steps are uncommitted until start_employment
    commits at the end, so any validation failure leaves nothing committed.

    Args:
        session: Database session
        old_user_id: User ID of person leaving
        new_user_id: User ID of person starting
        person_id: Position in rotation (1-10)
        new_name: New person's name
        new_username: New person's username
        effective_from: Date when new person starts
        created_by: Admin user ID creating this change
        old_end_date: Last day of the old person's employment. Defaults to the
            day before effective_from. Supply an earlier date to leave a gap
            between the old person's last day and the new person's first day.
            Must be before effective_from.

    Returns:
        The newly created PersonHistory record

    Raises:
        ValueError: If old_end_date is not before effective_from, if it
            predates the old person's employment start, or if the position is
            still held by someone other than old_user_id.

    Example:
        >>> # Person 6 (Kalle, user_id=6) leaves 2026-03-31
        >>> # Anna (user_id=11) starts 2026-04-01
        >>> new_record = add_person_change(
        ...     db, old_user_id=6, new_user_id=11, person_id=6,
        ...     new_name="Anna", new_username="abc123",
        ...     effective_from=date(2026, 4, 1), created_by=1
        ... )
    """
    if old_end_date is None:
        old_end_date = effective_from - timedelta(days=1)
    if old_end_date >= effective_from:
        raise ValueError(f"The old person's end date {old_end_date} must be before the new start {effective_from}.")

    # Close old person's record
    old_record = (
        session.query(PersonHistory)
        .filter(
            PersonHistory.user_id == old_user_id,
            PersonHistory.person_id == person_id,
            PersonHistory.effective_to.is_(None),
        )
        .first()
    )

    if old_record:
        if old_end_date < old_record.effective_from:
            raise ValueError(f"End date {old_end_date} is before the employment start {old_record.effective_from}.")
        old_record.effective_to = old_end_date
        old_record.is_active = 0

    # Deactivate old user and clear their rotation position
    old_user = session.query(User).filter(User.id == old_user_id).first()
    if old_user:
        old_user.is_active = 0
        old_user.person_id = None

    # Make the closed record visible to the collision check below
    session.flush()

    # Reuse the validated start logic; it raises ValueError if the position
    # is still held (e.g. old_user_id did not match the actual holder).
    new_record = start_employment(
        session=session,
        user_id=new_user_id,
        person_id=person_id,
        name=new_name,
        username=new_username,
        start_date=effective_from,
        created_by=created_by,
    )
    return new_record


def end_employment(
    session: Session,
    user_id: int,
    person_id: int,
    end_date: date,
) -> PersonHistory | None:
    """
    End a person's employment without immediately replacing them.

    This function:
    1. Closes person's PersonHistory record (sets effective_to)
    2. Deactivates user (sets is_active=0)

    Args:
        session: Database session
        user_id: User ID of person leaving
        person_id: Position in rotation (1-10)
        end_date: Last day of employment

    Returns:
        The updated PersonHistory record, or None if no record found

    Example:
        >>> # Person 6 leaves 2026-03-31
        >>> record = end_employment(db, user_id=6, person_id=6, end_date=date(2026, 3, 31))
    """
    # Close person's record
    record = (
        session.query(PersonHistory)
        .filter(
            PersonHistory.user_id == user_id,
            PersonHistory.person_id == person_id,
            PersonHistory.effective_to.is_(None),
        )
        .first()
    )

    if record and end_date < record.effective_from:
        raise ValueError(f"End date {end_date} is before the employment start {record.effective_from}.")

    if record:
        record.effective_to = end_date
        record.is_active = 0

    # Deactivate user and clear their rotation position
    user = session.query(User).filter(User.id == user_id).first()
    if user:
        user.is_active = 0
        user.person_id = None

    session.commit()
    return record


def _create_employment_record(
    session: Session,
    user_id: int,
    person_id: int,
    name: str,
    username: str,
    start_date: date,
    created_by: int,
) -> PersonHistory:
    """Validate and create an employment record without committing.

    Shared by start_employment (public, commits) and swap_positions (which
    must commit two crossed records atomically).
    """
    # One open record per position: reject if someone already holds it
    open_at_position = (
        session.query(PersonHistory)
        .filter(
            PersonHistory.person_id == person_id,
            PersonHistory.effective_to.is_(None),
        )
        .first()
    )
    if open_at_position:
        raise ValueError(
            f"Position {person_id} is already held by {open_at_position.name} "
            f"(since {open_at_position.effective_from}). End that employment first."
        )

    # One open record per user: a person cannot hold two positions
    open_for_user = (
        session.query(PersonHistory)
        .filter(
            PersonHistory.user_id == user_id,
            PersonHistory.effective_to.is_(None),
        )
        .first()
    )
    if open_for_user:
        raise ValueError(f"This user already has an open employment at position {open_for_user.person_id}.")

    # Create new person's record
    new_record = PersonHistory(
        user_id=user_id,
        person_id=person_id,
        name=name,
        username=username,
        is_active=1,
        effective_from=start_date,
        effective_to=None,
        created_by=created_by,
    )
    session.add(new_record)

    # Activate user and set their rotation position
    user = session.query(User).filter(User.id == user_id).first()
    if user:
        user.is_active = 1
        user.name = name
        user.username = username
        user.person_id = person_id  # Set the rotation position
        # Keep the vacation-balance start date in sync. An already populated
        # value may deliberately predate the rotation (e.g. consultant history),
        # so only fill it when empty.
        if user.employment_start_date is None:
            user.employment_start_date = start_date

    return new_record


def start_employment(
    session: Session,
    user_id: int,
    person_id: int,
    name: str,
    username: str,
    start_date: date,
    created_by: int,
) -> PersonHistory:
    """
    Start a new person's employment at a position.

    This function:
    1. Creates new PersonHistory record
    2. Activates user (sets is_active=1)
    3. Updates user's name and username

    Args:
        session: Database session
        user_id: User ID of person starting
        person_id: Position in rotation (1-10)
        name: Person's name
        username: Person's username
        start_date: First day of employment
        created_by: Admin user ID creating this change

    Returns:
        The newly created PersonHistory record

    Example:
        >>> # Anna (user_id=11) starts at position 6 on 2026-04-01
        >>> record = start_employment(
        ...     db, user_id=11, person_id=6,
        ...     name="Anna", username="abc123",
        ...     start_date=date(2026, 4, 1), created_by=1
        ... )
    """
    new_record = _create_employment_record(session, user_id, person_id, name, username, start_date, created_by)
    session.commit()
    return new_record


def swap_positions(
    session: Session,
    person_id_a: int,
    person_id_b: int,
    swap_date: date,
    created_by: int,
) -> tuple[PersonHistory, PersonHistory]:
    """
    Two current employees trade rotation positions on a date.

    Closes both open records the day before swap_date, then opens two new
    records with the holders crossed. The whole swap commits once; any
    validation failure leaves nothing committed.
    """
    if person_id_a == person_id_b:
        raise ValueError("Cannot swap a position with itself.")

    def _open_record(pid: int) -> PersonHistory:
        record = (
            session.query(PersonHistory)
            .filter(PersonHistory.person_id == pid, PersonHistory.effective_to.is_(None))
            .first()
        )
        if record is None:
            raise ValueError(f"Position {pid} has no current holder to swap.")
        return record

    rec_a = _open_record(person_id_a)
    rec_b = _open_record(person_id_b)

    last_day = swap_date - timedelta(days=1)
    for rec in (rec_a, rec_b):
        if last_day < rec.effective_from:
            raise ValueError(
                f"Swap date {swap_date} is not after the employment start {rec.effective_from} "
                f"at position {rec.person_id}."
            )

    rec_a.effective_to = last_day
    rec_a.is_active = 0
    rec_b.effective_to = last_day
    rec_b.is_active = 0
    session.flush()

    new_b_at_a = _create_employment_record(
        session, rec_b.user_id, person_id_a, rec_b.name, rec_b.username, swap_date, created_by
    )
    new_a_at_b = _create_employment_record(
        session, rec_a.user_id, person_id_b, rec_a.name, rec_a.username, swap_date, created_by
    )
    session.commit()
    return new_b_at_a, new_a_at_b


def update_employment_dates(
    session: Session,
    history_id: int,
    effective_from: date,
    effective_to: date | None,
) -> PersonHistory:
    """
    Edit the date range of an employment record, rotation-era style.

    Validates against sibling records on the same position (no overlaps, at
    most one open record). Closing the currently open record deactivates the
    user; reopening a record activates the user at the position. The user's
    employment_start_date is not modified by edits.
    """
    record = session.query(PersonHistory).filter(PersonHistory.id == history_id).first()
    if record is None:
        raise ValueError(f"Employment record {history_id} not found.")

    if effective_to is not None and effective_to < effective_from:
        raise ValueError(f"End date {effective_to} is before the start date {effective_from}.")

    siblings = (
        session.query(PersonHistory)
        .filter(PersonHistory.person_id == record.person_id, PersonHistory.id != record.id)
        .all()
    )
    for sib in siblings:
        if effective_to is None and sib.effective_to is None:
            raise ValueError(f"Position {record.person_id} already has an open employment ({sib.name}).")
        sib_end = sib.effective_to or date.max
        new_end = effective_to or date.max
        if effective_from <= sib_end and sib.effective_from <= new_end:
            raise ValueError(
                f"The dates overlap {sib.name}'s employment "
                f"({sib.effective_from} to {sib.effective_to or 'open'}) at position {record.person_id}."
            )

    was_open = record.effective_to is None
    record.effective_from = effective_from
    record.effective_to = effective_to

    user = session.query(User).filter(User.id == record.user_id).first()
    if effective_to is not None:
        record.is_active = 0
        if was_open and user:
            user.is_active = 0
            user.person_id = None
    else:
        record.is_active = 1
        if user:
            user.is_active = 1
            user.person_id = record.person_id

    session.commit()
    return record


def get_person_history(session: Session, person_id: int) -> list[dict]:
    """
    Get all historical records for a position (person_id).

    Args:
        session: Database session
        person_id: Position in rotation (1-10)

    Returns:
        List of dicts with employment history, sorted by most recent first.
        Each dict contains: id, user_id, name, username, is_active,
        effective_from, effective_to, is_current

    Example:
        >>> history = get_person_history(db, person_id=6)
        >>> for record in history:
        ...     print(f"{record['name']}: {record['effective_from']} to {record['effective_to']}")
        # Anna: 2026-04-01 to None
        # Kalle: 2026-01-06 to 2026-03-31
    """
    records = (
        session.query(PersonHistory)
        .filter(PersonHistory.person_id == person_id)
        .order_by(PersonHistory.effective_from.desc())
        .all()
    )

    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "name": r.name,
            "username": r.username,
            "is_active": r.is_active,
            "effective_from": r.effective_from,
            "effective_to": r.effective_to,
            "is_current": r.effective_to is None,
        }
        for r in records
    ]


def get_user_history(session: Session, user_id: int) -> list[dict]:
    """
    Get all historical employment records for a specific user.

    Args:
        session: Database session
        user_id: User ID

    Returns:
        List of dicts with user's employment history across all positions,
        sorted by most recent first.

    Example:
        >>> history = get_user_history(db, user_id=6)
        >>> for record in history:
        ...     print(f"Position {record['person_id']}: {record['effective_from']} to {record['effective_to']}")
        # Position 6: 2026-01-06 to 2026-03-31
    """
    records = (
        session.query(PersonHistory)
        .filter(PersonHistory.user_id == user_id)
        .order_by(PersonHistory.effective_from.desc())
        .all()
    )

    return [
        {
            "id": r.id,
            "person_id": r.person_id,
            "name": r.name,
            "username": r.username,
            "is_active": r.is_active,
            "effective_from": r.effective_from,
            "effective_to": r.effective_to,
            "is_current": r.effective_to is None,
        }
        for r in records
    ]


def get_user_person_id(session: Session, user_id: int) -> int | None:
    """
    Get the person_id (rotation position) for a user.

    Looks up PersonHistory to find which position the user occupies or occupied.
    Returns the most recent person_id assignment.

    Args:
        session: Database session
        user_id: User ID

    Returns:
        The person_id (1-10) or None if no assignment found.

    Example:
        >>> # Rickard (user_id=11) has person_id=3
        >>> pid = get_user_person_id(db, user_id=11)
        >>> print(pid)  # 3
    """
    record = (
        session.query(PersonHistory)
        .filter(PersonHistory.user_id == user_id)
        .order_by(PersonHistory.effective_from.desc())
        .first()
    )

    if record:
        return record.person_id

    # Fallback: if no PersonHistory, assume user_id == person_id (legacy behavior)
    user = session.query(User).filter(User.id == user_id).first()
    if user and user_id <= 10:
        return user_id

    return None


def user_can_view_person(session: Session, user_id: int, person_id: int) -> bool:
    """
    Check if a user is allowed to view a specific person_id's schedule.

    A user can view a person_id if:
    - They have (or had) that person_id via PersonHistory
    - Their user_id matches person_id (legacy compatibility)

    Args:
        session: Database session
        user_id: User ID of the viewer
        person_id: Position in rotation (1-10) to view

    Returns:
        True if user can view, False otherwise.

    Example:
        >>> # Rickard (user_id=11) can view person_id=3
        >>> can_view = user_can_view_person(db, user_id=11, person_id=3)
        >>> print(can_view)  # True
    """
    # Check PersonHistory for any assignment to this person_id
    record = (
        session.query(PersonHistory)
        .filter(
            PersonHistory.user_id == user_id,
            PersonHistory.person_id == person_id,
        )
        .first()
    )

    if record:
        return True

    # Legacy fallback: user_id == person_id
    if user_id == person_id:
        return True

    return False


def get_current_person_for_position(session: Session, person_id: int) -> dict | None:
    """
    Get the currently active person at a position.

    Args:
        session: Database session
        person_id: Position in rotation (1-10)

    Returns:
        Dict with person data, or None if position is vacant.

    Example:
        >>> person = get_current_person_for_position(db, person_id=3)
        >>> print(person["name"])  # "Rickard" (current holder of position 3)
    """
    record = (
        session.query(PersonHistory)
        .filter(
            PersonHistory.person_id == person_id,
            PersonHistory.effective_to.is_(None),
        )
        .first()
    )

    if record:
        return {
            "user_id": record.user_id,
            "name": record.name,
            "username": record.username,
            "effective_from": record.effective_from,
        }

    # Fallback to User table
    user = session.query(User).filter(User.id == person_id).first()
    if user:
        return {
            "user_id": user.id,
            "name": user.name,
            "username": user.username,
            "effective_from": None,
        }

    return None


def is_date_before_employment(session: Session, person_id: int, check_date: date) -> bool:
    """
    Check if a date is before the current person's employment started at a position.

    Used to determine if "OFF" should be shown for dates before a new employee started.

    Args:
        session: Database session
        person_id: Position in rotation (1-10)
        check_date: Date to check

    Returns:
        True if the date is before the current person's effective_from.

    Example:
        >>> # Rickard started 2026-01-26 at position 3
        >>> is_before = is_date_before_employment(db, person_id=3, check_date=date(2026, 1, 20))
        >>> print(is_before)  # True
    """
    # Get the current (or most recent) person at this position
    current = get_current_person_for_position(session, person_id)

    if current and current.get("effective_from"):
        return check_date < current["effective_from"]

    return False


def get_position_vacancy(session: Session, person_id: int, check_date: date) -> PersonHistory | None:
    """
    Return the last closed PersonHistory record if a position is vacant on a date.

    A position is vacant when it has employment history, the most recent record
    is closed (effective_to set), and check_date falls after that end date.
    Positions without any history keep legacy behavior and are never vacant.

    Used by schedule rendering to show OFF for rotation days after the last
    holder's employment ended with no successor.
    """
    latest = (
        session.query(PersonHistory)
        .filter(PersonHistory.person_id == person_id)
        .order_by(PersonHistory.effective_from.desc())
        .first()
    )
    if latest is None or latest.effective_to is None:
        return None
    if check_date > latest.effective_to:
        return latest
    return None


def get_position_holder_segments(session: Session, person_id: int, start_date: date, end_date: date) -> list[dict]:
    """
    Return the employment segments overlapping a date window for a position.

    Each segment is a dict with user_id, name, username, from_date and to_date,
    where the dates are clamped to [start_date, end_date]. Segments are ordered
    by effective_from ascending. Positions without overlapping history return
    an empty list; combine with has_position_history to distinguish a vacant
    position from a legacy position that never had history records.

    Used by team views to render one column/row per holder when a person
    change happens mid-period.
    """
    records = (
        session.query(PersonHistory)
        .filter(
            PersonHistory.person_id == person_id,
            PersonHistory.effective_from <= end_date,
            (PersonHistory.effective_to.is_(None)) | (PersonHistory.effective_to >= start_date),
        )
        .order_by(PersonHistory.effective_from.asc())
        .all()
    )
    return [
        {
            "user_id": r.user_id,
            "name": r.name,
            "username": r.username,
            "from_date": max(r.effective_from, start_date),
            "to_date": min(r.effective_to, end_date) if r.effective_to else end_date,
        }
        for r in records
    ]


def has_position_history(session: Session, person_id: int) -> bool:
    """Check whether a position has any PersonHistory records at all."""
    return session.query(PersonHistory.id).filter(PersonHistory.person_id == person_id).first() is not None
