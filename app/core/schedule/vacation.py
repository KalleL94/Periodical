"""Semesterhantering."""

import datetime

from app.core.constants import PERSON_IDS
from app.core.storage import load_persons


def get_vacation_dates_for_year(year: int) -> dict[int, set[datetime.date]]:
    """
    Hämtar semesterdatum för alla personer för ett år.

    Returns:
        Dict med person_id -> set av semesterdatum
    """
    from app.database.database import SessionLocal, User

    persons = load_persons()
    per_person: dict[int, set[datetime.date]] = {p.id: set() for p in persons}

    db = SessionLocal()
    try:
        users = db.query(User).filter(User.id.in_(PERSON_IDS)).all()

        for user in users:
            vac_by_year = user.vacation or {}
            weeks_for_year = vac_by_year.get(str(year), []) or []

            for week in weeks_for_year:
                for day in range(1, 8):
                    try:
                        d = datetime.date.fromisocalendar(year, week, day)
                        per_person[user.id].add(d)
                    except ValueError:
                        # Ogiltig vecka för året, ignorera
                        continue
    finally:
        db.close()

    return per_person
