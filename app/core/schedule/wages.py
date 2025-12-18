"""Lönehantering från databas."""

from app.core.constants import PERSON_IDS


def get_user_wage(session, user_id: int, fallback: int | None = None) -> int:
    """
    Hämtar en användares lön från databasen.

    Args:
        session: SQLAlchemy session
        user_id: Användar-ID
        fallback: Fallback-värde om användare saknas

    Returns:
        Lön i SEK
    """
    from .core import get_settings

    settings = get_settings()
    default = fallback or settings.monthly_salary

    if not session:
        return default

    from app.database.database import User

    user = session.query(User).filter(User.id == user_id).first()
    return user.wage if user else default


def get_all_user_wages(session) -> dict[int, int]:
    """
    Hämtar alla användares löner i en query.

    Mer effektivt för batch-operationer än att anropa get_user_wage() i loop.

    Returns:
        Dict med user_id -> lön
    """
    from app.core.storage import load_persons

    from .core import get_settings

    settings = get_settings()

    if not session:
        persons = load_persons()
        return {p.id: p.wage for p in persons}

    from app.database.database import User

    users = session.query(User).filter(User.id.in_(PERSON_IDS)).all()
    wages = {user.id: user.wage for user in users}

    # Fyll i saknade med fallback
    for pid in PERSON_IDS:
        if pid not in wages:
            wages[pid] = settings.monthly_salary

    return wages
