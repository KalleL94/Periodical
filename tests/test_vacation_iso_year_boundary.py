"""Week-based leave is stored per ISO year but requested per calendar year."""

import datetime

from app.core.schedule.vacation import get_vacation_dates_for_year


def _holder_dates(test_db, test_user, year):
    test_user.person_id = 1
    test_user.vacation = {"2025": [1]}
    test_db.commit()
    return get_vacation_dates_for_year(year, session=test_db)[1]


def test_iso_week_1_spills_into_previous_calendar_year(test_db, test_user):
    assert datetime.date(2024, 12, 30) in _holder_dates(test_db, test_user, 2024)


def test_iso_week_1_in_requested_year(test_db, test_user):
    dates = _holder_dates(test_db, test_user, 2025)
    assert datetime.date(2025, 1, 1) in dates
    assert datetime.date(2024, 12, 30) not in dates
