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
    from app.core.schedule.core import get_settings

    settings = get_settings()
    return (settings.rotation_start_date, None)


def add_person_change(
    session: Session,
    old_user_id: int,
    new_user_id: int,
    person_id: int,
    new_name: str,
    new_username: str,
    effective_from: date,
    created_by: int,
) -> PersonHistory:
    """
    Register a person change (someone leaving, someone else starting).

    This function:
    1. Closes old person's PersonHistory record (sets effective_to)
    2. Deactivates old user (sets is_active=0)
    3. Creates new person's PersonHistory record
    4. Activates new user (sets is_active=1)

    Args:
        session: Database session
        old_user_id: User ID of person leaving
        new_user_id: User ID of person starting
        person_id: Position in rotation (1-10)
        new_name: New person's name
        new_username: New person's username
        effective_from: Date when new person starts
        created_by: Admin user ID creating this change

    Returns:
        The newly created PersonHistory record

    Example:
        >>> # Person 6 (Kalle, user_id=6) leaves 2026-03-31
        >>> # Anna (user_id=11) starts 2026-04-01
        >>> new_record = add_person_change(
        ...     db, old_user_id=6, new_user_id=11, person_id=6,
        ...     new_name="Anna", new_username="abc123",
        ...     effective_from=date(2026, 4, 1), created_by=1
        ... )
    """
    # Close old person's record (end date is day before new person starts)
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
        old_record.effective_to = effective_from - timedelta(days=1)
        old_record.is_active = 0

    # Deactivate old user
    old_user = session.query(User).filter(User.id == old_user_id).first()
    if old_user:
        old_user.is_active = 0

    # Create new person's record
    new_record = PersonHistory(
        user_id=new_user_id,
        person_id=person_id,
        name=new_name,
        username=new_username,
        is_active=1,
        effective_from=effective_from,
        effective_to=None,
        created_by=created_by,
    )
    session.add(new_record)

    # Activate new user
    new_user = session.query(User).filter(User.id == new_user_id).first()
    if new_user:
        new_user.is_active = 1
        new_user.name = new_name
        new_user.username = new_username

    session.commit()
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

    if record:
        record.effective_to = end_date
        record.is_active = 0

    # Deactivate user
    user = session.query(User).filter(User.id == user_id).first()
    if user:
        user.is_active = 0

    session.commit()
    return record


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

    # Activate user
    user = session.query(User).filter(User.id == user_id).first()
    if user:
        user.is_active = 1
        user.name = name
        user.username = username

    session.commit()
    return new_record


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
