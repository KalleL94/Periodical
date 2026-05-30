"""Regression tests for karens (waiting-day) budget handling in monthly absence deductions.

Focus: the karens budget (KARENS_HOURS = 8h per sick period) must be tracked across
month boundaries. A sick period that starts in one month and continues into the next
must NOT recharge the karens budget in the new month (bug B1).
"""

import datetime

import pytest

from app.core.schedule import wages
from app.core.schedule.wages import get_absence_deductions_for_month
from app.database.database import Absence, AbsenceType

# hourly wage = 17333 / 173.33 = 100.0 SEK/h exactly
MONTHLY_WAGE = 17333
HOURLY = MONTHLY_WAGE / 173.33  # ~= 100.0
SHIFT_HOURS = 8.0  # equals the full karens budget -> one full day consumes it


@pytest.fixture
def fixed_shift(monkeypatch):
    """Force every day to resolve to an 8.0h shift with no start/end datetimes.

    Patching the module-level name covers both get_absence_deductions_for_month and
    get_karens_consumed_before_date, which both look up shift times this way.
    None start/end means the OB branch is skipped, keeping deductions deterministic.
    """
    monkeypatch.setattr(wages, "get_shift_times_for_date", lambda session, user_id, d: (SHIFT_HOURS, None, None))


def _add_sick(db, user_id, *dates):
    for d in dates:
        db.add(Absence(user_id=user_id, date=d, absence_type=AbsenceType.SICK))
    db.commit()


def test_karens_not_recharged_across_month_boundary(test_db, fixed_shift):
    """A sick period spanning Jan->Feb must consume the 8h karens budget only once."""
    user_id = 1
    # Jan 31 (period start, karens consumed) -> Feb 1 (gap 1 day, same period)
    _add_sick(test_db, user_id, datetime.date(2026, 1, 31), datetime.date(2026, 2, 1))

    jan = get_absence_deductions_for_month(test_db, user_id, 2026, 1, MONTHLY_WAGE)
    feb = get_absence_deductions_for_month(test_db, user_id, 2026, 2, MONTHLY_WAGE)

    # January: first sick day consumes the full 8h karens (100% deduction)
    jan_day = jan["details"][0]
    assert jan_day["karens_hours"] == pytest.approx(8.0)
    assert jan_day["deduction"] == pytest.approx(HOURLY * 8.0)  # 800

    # February: budget already spent -> no karens, only 20% sjuklön deduction
    feb_day = feb["details"][0]
    assert feb_day["karens_hours"] == pytest.approx(0.0)
    assert feb_day["is_karens"] is False
    assert feb_day["deduction"] == pytest.approx(HOURLY * 8.0 * 0.2)  # 160


def test_karens_consumed_once_within_month(test_db, fixed_shift):
    """Two consecutive sick days in one month: karens charged on day 1 only."""
    user_id = 1
    _add_sick(test_db, user_id, datetime.date(2026, 3, 2), datetime.date(2026, 3, 3))

    res = get_absence_deductions_for_month(test_db, user_id, 2026, 3, MONTHLY_WAGE)

    day1, day2 = res["details"]
    assert day1["karens_hours"] == pytest.approx(8.0)
    assert day1["deduction"] == pytest.approx(HOURLY * 8.0)  # 800 full
    assert day2["karens_hours"] == pytest.approx(0.0)
    assert day2["deduction"] == pytest.approx(HOURLY * 8.0 * 0.2)  # 160 sjuklön


def test_karens_resets_after_gap_over_five_days(test_db, fixed_shift):
    """A gap > 5 days starts a new sick period and recharges the karens budget."""
    user_id = 1
    # Mar 2, then Mar 12 (gap of 10 days) -> two separate periods, both charge karens
    _add_sick(test_db, user_id, datetime.date(2026, 3, 2), datetime.date(2026, 3, 12))

    res = get_absence_deductions_for_month(test_db, user_id, 2026, 3, MONTHLY_WAGE)

    day1, day2 = res["details"]
    assert day1["karens_hours"] == pytest.approx(8.0)
    assert day2["karens_hours"] == pytest.approx(8.0)
    assert day1["deduction"] == pytest.approx(HOURLY * 8.0)
    assert day2["deduction"] == pytest.approx(HOURLY * 8.0)
