"""Unit tests for employment lifecycle primitives in person_history.py.

Covers collision checks (one open record per position and per user),
employment_start_date sync, and end-date validation.
"""

import datetime

import pytest

from app.core.schedule.person_history import (
    add_person_change,  # noqa: F401  # used by Task 2 tests added in this file
    end_employment,
    get_position_holder_segments,
    get_position_vacancy,
    has_position_history,
    start_employment,
    swap_positions,
    update_employment_dates,
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


class TestAddPersonChange:
    def _setup_holder(self, test_db):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=1)
        return anna

    def test_swap_closes_old_and_opens_new(self, test_db):
        anna = self._setup_holder(test_db)
        bert = _make_user(test_db, 12, "bert1", "Bert")

        add_person_change(
            test_db,
            old_user_id=anna.id,
            new_user_id=bert.id,
            person_id=3,
            new_name="Bert",
            new_username="bert1",
            effective_from=datetime.date(2026, 4, 1),
            created_by=1,
        )

        old_rec = test_db.query(PersonHistory).filter(PersonHistory.user_id == anna.id).one()
        assert old_rec.effective_to == datetime.date(2026, 3, 31)

        new_rec = (
            test_db.query(PersonHistory)
            .filter(PersonHistory.person_id == 3, PersonHistory.effective_to.is_(None))
            .one()
        )
        assert new_rec.user_id == bert.id

        test_db.refresh(anna)
        test_db.refresh(bert)
        assert anna.is_active == 0 and anna.person_id is None
        assert bert.is_active == 1 and bert.person_id == 3
        assert bert.employment_start_date == datetime.date(2026, 4, 1)

    def test_swap_with_gap_uses_old_end_date(self, test_db):
        anna = self._setup_holder(test_db)
        bert = _make_user(test_db, 12, "bert1", "Bert")

        add_person_change(
            test_db,
            old_user_id=anna.id,
            new_user_id=bert.id,
            person_id=3,
            new_name="Bert",
            new_username="bert1",
            effective_from=datetime.date(2026, 4, 15),
            created_by=1,
            old_end_date=datetime.date(2026, 3, 31),
        )

        old_rec = test_db.query(PersonHistory).filter(PersonHistory.user_id == anna.id).one()
        assert old_rec.effective_to == datetime.date(2026, 3, 31)

    def test_rejects_end_date_not_before_start(self, test_db):
        anna = self._setup_holder(test_db)
        bert = _make_user(test_db, 12, "bert1", "Bert")

        with pytest.raises(ValueError, match="before"):
            add_person_change(
                test_db,
                old_user_id=anna.id,
                new_user_id=bert.id,
                person_id=3,
                new_name="Bert",
                new_username="bert1",
                effective_from=datetime.date(2026, 4, 1),
                created_by=1,
                old_end_date=datetime.date(2026, 4, 10),
            )

    def test_rejects_wrong_old_user(self, test_db):
        self._setup_holder(test_db)  # Anna holds position 3
        bert = _make_user(test_db, 12, "bert1", "Bert")
        casey = _make_user(test_db, 13, "casey1", "Casey")

        # Claiming Bert leaves position 3 must fail: Anna holds it
        with pytest.raises(ValueError, match="Position 3"):
            add_person_change(
                test_db,
                old_user_id=bert.id,
                new_user_id=casey.id,
                person_id=3,
                new_name="Casey",
                new_username="casey1",
                effective_from=datetime.date(2026, 4, 1),
                created_by=1,
            )


class TestGetPositionVacancy:
    def _employ_and_end(self, test_db, end):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=1)
        end_employment(test_db, anna.id, 3, end_date=end)
        return anna

    def test_vacant_after_last_employment_ended(self, test_db):
        self._employ_and_end(test_db, datetime.date(2026, 8, 4))

        vacancy = get_position_vacancy(test_db, 3, datetime.date(2026, 8, 5))
        assert vacancy is not None
        assert vacancy.name == "Anna"
        assert get_position_vacancy(test_db, 3, datetime.date(2026, 9, 15)) is not None

    def test_not_vacant_during_or_on_last_day(self, test_db):
        self._employ_and_end(test_db, datetime.date(2026, 8, 4))

        assert get_position_vacancy(test_db, 3, datetime.date(2026, 6, 1)) is None
        assert get_position_vacancy(test_db, 3, datetime.date(2026, 8, 4)) is None

    def test_not_vacant_with_open_employment(self, test_db):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=1)

        assert get_position_vacancy(test_db, 3, datetime.date(2027, 1, 1)) is None

    def test_no_history_keeps_legacy_behavior(self, test_db):
        assert get_position_vacancy(test_db, 7, datetime.date(2026, 8, 5)) is None

    def test_not_vacant_when_successor_took_over(self, test_db):
        anna = self._employ_and_end(test_db, datetime.date(2026, 8, 4))  # noqa: F841
        bert = _make_user(test_db, 12, "bert1", "Bert")
        start_employment(test_db, bert.id, 3, "Bert", "bert1", datetime.date(2026, 8, 5), created_by=1)

        assert get_position_vacancy(test_db, 3, datetime.date(2026, 9, 1)) is None


class TestPositionHolderSegments:
    def test_two_segments_for_mid_window_swap(self, test_db):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        bert = _make_user(test_db, 12, "bert1", "Bert")
        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=1)
        add_person_change(
            test_db,
            old_user_id=anna.id,
            new_user_id=bert.id,
            person_id=3,
            new_name="Bert",
            new_username="bert1",
            effective_from=datetime.date(2026, 8, 15),
            created_by=1,
        )

        segs = get_position_holder_segments(test_db, 3, datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))

        assert [s["name"] for s in segs] == ["Anna", "Bert"]
        assert segs[0]["from_date"] == datetime.date(2026, 8, 1)  # clamped
        assert segs[0]["to_date"] == datetime.date(2026, 8, 14)
        assert segs[1]["from_date"] == datetime.date(2026, 8, 15)
        assert segs[1]["to_date"] == datetime.date(2026, 8, 31)  # open record clamped

    def test_no_overlap_after_departure(self, test_db):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        start_employment(test_db, anna.id, 5, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=1)
        end_employment(test_db, anna.id, 5, end_date=datetime.date(2026, 8, 4))

        segs = get_position_holder_segments(test_db, 5, datetime.date(2026, 9, 1), datetime.date(2026, 9, 30))

        assert segs == []
        assert has_position_history(test_db, 5) is True

    def test_no_history_position(self, test_db):
        assert get_position_holder_segments(test_db, 7, datetime.date(2026, 8, 1), datetime.date(2026, 8, 31)) == []
        assert has_position_history(test_db, 7) is False


class TestSwapPositions:
    def _two_holders(self, test_db):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        bert = _make_user(test_db, 12, "bert1", "Bert")
        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=1)
        start_employment(test_db, bert.id, 5, "Bert", "bert1", datetime.date(2026, 2, 1), created_by=1)
        return anna, bert

    def test_swap_crosses_positions(self, test_db):
        anna, bert = self._two_holders(test_db)

        swap_positions(test_db, 3, 5, datetime.date(2026, 9, 1), created_by=1)

        open_3 = (
            test_db.query(PersonHistory)
            .filter(PersonHistory.person_id == 3, PersonHistory.effective_to.is_(None))
            .one()
        )
        open_5 = (
            test_db.query(PersonHistory)
            .filter(PersonHistory.person_id == 5, PersonHistory.effective_to.is_(None))
            .one()
        )
        assert open_3.user_id == bert.id and open_3.effective_from == datetime.date(2026, 9, 1)
        assert open_5.user_id == anna.id and open_5.effective_from == datetime.date(2026, 9, 1)

        closed_3 = (
            test_db.query(PersonHistory).filter(PersonHistory.person_id == 3, PersonHistory.user_id == anna.id).one()
        )
        assert closed_3.effective_to == datetime.date(2026, 8, 31)

        test_db.refresh(anna)
        test_db.refresh(bert)
        assert anna.person_id == 5 and bert.person_id == 3
        assert anna.is_active == 1 and bert.is_active == 1
        # employment_start_date untouched by a position swap
        assert anna.employment_start_date == datetime.date(2026, 1, 1)

    def test_swap_rejects_vacant_position(self, test_db):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=1)

        with pytest.raises(ValueError):
            swap_positions(test_db, 3, 5, datetime.date(2026, 9, 1), created_by=1)

    def test_swap_rejects_same_position(self, test_db):
        self._two_holders(test_db)
        with pytest.raises(ValueError):
            swap_positions(test_db, 3, 3, datetime.date(2026, 9, 1), created_by=1)

    def test_swap_rejects_date_before_either_start(self, test_db):
        self._two_holders(test_db)  # Bert started 2026-02-01
        with pytest.raises(ValueError):
            swap_positions(test_db, 3, 5, datetime.date(2026, 1, 15), created_by=1)


class TestUpdateEmploymentDates:
    def _closed_and_open(self, test_db):
        anna = _make_user(test_db, 11, "anna1", "Anna")
        bert = _make_user(test_db, 12, "bert1", "Bert")
        start_employment(test_db, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 1), created_by=1)
        add_person_change(
            test_db,
            old_user_id=anna.id,
            new_user_id=bert.id,
            person_id=3,
            new_name="Bert",
            new_username="bert1",
            effective_from=datetime.date(2026, 8, 15),
            created_by=1,
        )
        anna_rec = test_db.query(PersonHistory).filter(PersonHistory.user_id == anna.id).one()
        bert_rec = test_db.query(PersonHistory).filter(PersonHistory.user_id == bert.id).one()
        return anna, bert, anna_rec, bert_rec

    def test_edit_dates_within_free_range(self, test_db):
        anna, bert, anna_rec, bert_rec = self._closed_and_open(test_db)

        updated = update_employment_dates(test_db, anna_rec.id, datetime.date(2026, 2, 1), datetime.date(2026, 8, 1))

        assert updated.effective_from == datetime.date(2026, 2, 1)
        assert updated.effective_to == datetime.date(2026, 8, 1)

    def test_rejects_overlap_with_sibling(self, test_db):
        anna, bert, anna_rec, bert_rec = self._closed_and_open(test_db)

        with pytest.raises(ValueError):
            update_employment_dates(test_db, anna_rec.id, datetime.date(2026, 1, 1), datetime.date(2026, 8, 20))

    def test_rejects_reversed_dates(self, test_db):
        anna, bert, anna_rec, bert_rec = self._closed_and_open(test_db)

        with pytest.raises(ValueError):
            update_employment_dates(test_db, anna_rec.id, datetime.date(2026, 6, 1), datetime.date(2026, 5, 1))

    def test_rejects_second_open_record(self, test_db):
        anna, bert, anna_rec, bert_rec = self._closed_and_open(test_db)

        with pytest.raises(ValueError):
            update_employment_dates(test_db, anna_rec.id, datetime.date(2026, 1, 1), None)

    def test_closing_open_record_deactivates_user(self, test_db):
        anna, bert, anna_rec, bert_rec = self._closed_and_open(test_db)

        update_employment_dates(test_db, bert_rec.id, datetime.date(2026, 8, 15), datetime.date(2026, 12, 31))

        test_db.refresh(bert)
        assert bert.is_active == 0
        assert bert.person_id is None
