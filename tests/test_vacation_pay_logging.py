"""Regression test for audit item B5.

calculate_vacation_pay used to swallow exceptions from summarize_month_for_person with a
bare `except Exception: pass`, silently dropping that month's variable earnings (which feed
the 0.5% variable part of the vacation supplement). It must now log the failure while still
degrading gracefully instead of crashing the whole vacation calculation.
"""

import datetime
from unittest.mock import MagicMock

from app.core.schedule import vacation
from app.core.schedule.vacation import calculate_vacation_pay
from app.database.database import User, UserRole

RATES = {"fixed_pct": 0.008, "variable_pct": 0.005, "payout_pct": 0.046}


def test_failed_month_is_logged_not_silently_swallowed(test_db, monkeypatch):
    user = User(
        id=1,
        username="vacuser",
        password_hash="x",
        name="Vac User",
        role=UserRole.USER,
        wage=30000,
        person_id=1,
        vacation={},
        must_change_password=0,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    def boom(*args, **kwargs):
        raise RuntimeError("monthly summary exploded")

    monkeypatch.setattr("app.core.schedule.summary.summarize_month_for_person", boom)

    fake_logger = MagicMock()
    monkeypatch.setattr(vacation, "logger", fake_logger)

    result = calculate_vacation_pay(
        user=user,
        entitled_days=25,
        earning_start=datetime.date(2025, 1, 1),
        earning_end=datetime.date(2025, 1, 31),
        db=test_db,
        vacation_rates=RATES,
    )

    # Degraded gracefully: no crash, variable part excluded, fixed part still computed.
    assert result["variable_per_day"] == 0.0
    assert result["fixed_per_day"] == round(30000 * 0.008, 2)
    assert result["monthly_salary"] == 30000

    # The failure was logged rather than silently swallowed.
    assert fake_logger.warning.called
