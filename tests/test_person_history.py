"""Unit tests for employment lifecycle primitives in person_history.py.

Covers collision checks (one open record per position and per user),
employment_start_date sync, and end-date validation.
"""

import datetime

import pytest

from app.core.schedule.person_history import (
    add_person_change,  # noqa: F401  # used by Task 2 tests added in this file
    end_employment,
    start_employment,
)
from app.database.database import PersonHistory, User, UserRole


def _make_user(test_db, uid, username, name):
    user = User(
        id=uid,
        username=username,
        password_hash="x",
        name=name,
        role=UserRole.USER,
        wage=30000,
        vacation={},
        must_change_password=0,
        is_active=0,
    )
    test_db.add(user)
    test_db.commit()
    return user


class TestStartEmployment:
    def test_rejects_occupied_position(self, test_db):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        bert = _make_user(test_db, 12, "bert1", "Bert")
        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=1)

        with pytest.raises(ValueError, match="Position 3"):
            start_employment(test_db, bert.id, 3, "Bert", "bert1", datetime.date(2026, 2, 1), created_by=1)

        open_records = (
            test_db.query(PersonHistory)
            .filter(PersonHistory.person_id == 3, PersonHistory.effective_to.is_(None))
            .count()
        )
        assert open_records == 1

    def test_rejects_user_with_open_employment(self, test_db):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=1)

        with pytest.raises(ValueError, match="already has an open employment"):
            start_employment(test_db, anna.id, 4, "Anna", "anna1", datetime.date(2026, 2, 1), created_by=1)

    def test_sets_employment_start_date_when_null(self, test_db):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        assert anna.employment_start_date is None

        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=1)

        test_db.refresh(anna)
        assert anna.employment_start_date == datetime.date(2026, 1, 1)

    def test_keeps_existing_employment_start_date(self, test_db):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        anna.employment_start_date = datetime.date(2024, 6, 1)
        test_db.commit()

        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=1)

        test_db.refresh(anna)
        assert anna.employment_start_date == datetime.date(2024, 6, 1)


class TestEndEmployment:
    def test_rejects_end_date_before_start(self, test_db):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=1)

        with pytest.raises(ValueError, match="before"):
            end_employment(test_db, anna.id, 3, end_date=datetime.date(2025, 12, 1))

    def test_closes_record_and_deactivates_user(self, test_db):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=1)

        record = end_employment(test_db, anna.id, 3, end_date=datetime.date(2026, 3, 31))

        assert record.effective_to == datetime.date(2026, 3, 31)
        test_db.refresh(anna)
        assert anna.is_active == 0
        assert anna.person_id is None
