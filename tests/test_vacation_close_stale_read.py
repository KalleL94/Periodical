"""Closing a vacation year must not write on top of a stale vacation_saved.

The close runs on a GET: every view that shows a balance reaches
calculate_vacation_balance, which auto-closes a year that has ended. Between
reading user.vacation_saved and deciding to close, it runs a long stretch of
queries, so two requests for different years could each start from the same dict
and the second commit would silently drop the first year's close record.

close_vacation_year therefore re-reads the column from the row before building
its new dict. These pin that, by handing it a User object whose in-memory copy is
deliberately behind the database, which is exactly the shape the race produces.
"""

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.schedule.vacation import close_vacation_year
from app.database.database import Base, User, UserRole, WageType

PAY = {"monthly_salary": 30000, "supplement_per_day": 150.0, "payout_pct": 0.046}


@pytest.fixture()
def sessions(tmp_path):
    """Two sessions on one database, the way two concurrent requests see it.

    A file rather than :memory:, because a shared-connection in-memory engine would
    give both sessions the same connection and there would be no race to model.
    """
    eng = create_engine(f"sqlite:///{tmp_path / 'v.db'}")
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng)
    a, b = Session(), Session()
    yield a, b
    a.close()
    b.close()


@pytest.fixture()
def db(sessions):
    return sessions[0]


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


def test_a_concurrent_close_of_another_year_is_not_dropped(sessions):
    a, b = sessions
    _make_user(a)

    # Both requests load the user before either has closed anything.
    user_a = a.query(User).filter(User.id == 99).one()
    user_b = b.query(User).filter(User.id == 99).one()

    close_vacation_year(user_a, 2023, 8, PAY, a)
    close_vacation_year(user_b, 2024, 8, PAY, b)

    stored = b.query(User.vacation_saved).filter(User.id == 99).scalar()
    assert set(stored) == {"2023", "2024"}, "the earlier close was overwritten"
    assert stored["2023"]["saved"] == 5
    assert stored["2024"]["saved"] == 5


def test_a_concurrent_close_of_the_same_year_does_not_recompute_it(sessions):
    """Different remaining_own on purpose: a re-close would answer differently."""
    a, b = sessions
    _make_user(a)
    user_a = a.query(User).filter(User.id == 99).one()
    user_b = b.query(User).filter(User.id == 99).one()

    first = close_vacation_year(user_a, 2023, 8, PAY, a)
    second = close_vacation_year(user_b, 2023, 2, PAY, b)

    assert first["saved"] == 5, "8 remaining days: 5 saved, 3 paid out"
    assert second == first, "the second close recomputed the year instead of reading it"

    stored = b.query(User.vacation_saved).filter(User.id == 99).scalar()
    assert stored["2023"] == first


def test_overuse_consumption_is_not_doubled(sessions):
    """The overuse branch spends earlier years' saved days.

    Worth stating what is and is not at risk here: two closes of the SAME year from
    the same stale base compute the same consumption and converge on the same number,
    so this never double-spent even before the guard. What the guard protects is the
    OTHER year's record, above. This pins that the guard did not break the arithmetic.
    """
    a, b = sessions
    _make_user(a, vacation_saved={"2021": {"saved": 5, "paid_out": 0, "payout_amount": 0.0, "payout_per_day": 0.0}})
    user_a = a.query(User).filter(User.id == 99).one()
    user_b = b.query(User).filter(User.id == 99).one()

    close_vacation_year(user_a, 2023, -3, PAY, a)
    close_vacation_year(user_b, 2023, -3, PAY, b)

    stored = b.query(User.vacation_saved).filter(User.id == 99).scalar()
    assert stored["2021"]["saved"] == 2


def test_a_fresh_close_still_writes(db):
    """The guard must not turn every close into a no-op."""
    user = _make_user(db)

    closed = close_vacation_year(user, 2023, 8, PAY, db)

    stored = db.query(User.vacation_saved).filter(User.id == 99).scalar()
    assert closed["saved"] == 5
    assert closed["paid_out"] == 3
    assert stored["2023"] == closed
