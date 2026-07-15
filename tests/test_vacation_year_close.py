"""Closing a vacation year must only save/pay out the year's own remaining days.

Previously the close was fed entitled + saved_from_previous - used, which swept
earlier years' saved days (valid five years per semesterlagen paragraph 18) into
the cash payout while leaving their vacation_saved entries in place, so the same
days were both paid out and still counted as an available saved balance.
"""

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.schedule.vacation import (
    calculate_vacation_balance,
    close_vacation_year,
    get_saved_days_balance,
)
from app.database.database import Base, User, UserRole, WageType


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    session = sessionmaker(bind=eng)()
    yield session
    session.close()


def _make_user(db, *, vacation_saved=None):
    user = User(
        id=99,
        username="v",
        password_hash="x",
        name="V",
        role=UserRole.USER,
        wage=30000,
        wage_type=WageType.MONTHLY,
        vacation={},
        must_change_password=0,
        is_active=1,
        person_id=None,
        employment_start_date=datetime.date(2015, 1, 1),
        vacation_year_start_month=4,
        vacation_days_per_year=25,
        vacation_saved=vacation_saved or {},
    )
    db.add(user)
    db.commit()
    return user


_SAVED_2021 = {"2021": {"saved": 5, "paid_out": 0, "payout_amount": 0.0, "payout_per_day": 0.0}}


def test_close_pays_out_only_the_years_own_days(db):
    user = _make_user(db, vacation_saved=dict(_SAVED_2021))

    bal = calculate_vacation_balance(user, 2023, db)
    closed = bal["closed"]
    assert closed is not None, "2023 is past its year-end and must auto-close"

    # 25 entitled, 0 used: save 5, pay out 20. The 2021 saved days are untouched.
    assert closed["saved"] == 5
    assert closed["paid_out"] == 20
    assert (user.vacation_saved or {}).get("2021", {}).get("saved") == 5


def test_saved_days_are_not_double_counted_after_close(db):
    user = _make_user(db, vacation_saved=dict(_SAVED_2021))

    calculate_vacation_balance(user, 2023, db)

    # 2021's 5 (still saved) plus 2023's newly saved 5.
    assert get_saved_days_balance(user, 2024)["total_saved"] == 10


def test_projection_for_open_year_uses_only_own_days(db):
    user = _make_user(db, vacation_saved=dict(_SAVED_2021))

    bal = calculate_vacation_balance(user, 2026, db)
    projection = bal["projection"]
    assert projection is not None, "2026 vacation year is open and must project"

    # Projection must not sweep the 2021 saved days into the payout either.
    assert projection["days_to_save"] == 5
    assert projection["days_to_pay_out"] == 20


def test_overuse_consumes_saved_days_oldest_first(db):
    user = _make_user(
        db,
        vacation_saved={
            "2020": {"saved": 2, "paid_out": 0, "payout_amount": 0.0, "payout_per_day": 0.0},
            "2021": {"saved": 5, "paid_out": 0, "payout_amount": 0.0, "payout_per_day": 0.0},
        },
    )
    pay = {"monthly_salary": 30000, "supplement_per_day": 150.0, "payout_pct": 0.046}

    # Used 3 more days than the year's own entitlement.
    closed = close_vacation_year(user, 2023, -3, pay, db)

    assert closed["saved"] == 0
    assert closed["paid_out"] == 0
    assert closed["payout_amount"] == 0.0
    saved = user.vacation_saved
    assert saved["2020"]["saved"] == 0, "oldest saved year consumed first"
    assert saved["2021"]["saved"] == 4, "remainder taken from the next year"
