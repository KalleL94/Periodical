"""Late arrival as its own absence type, in a paid and an unpaid variant.

Recording "I came in at 07:00" as LEAVE reads on the schedule as if the whole day
was off, when nine of ten hours were worked. The money is deliberately identical
to the existing pair: LATE_UNPAID deducts like LEAVE, LATE_PAID does not, like
OFF. Only the name and the marker change.
"""

import datetime

import pytest

from app.core.schedule.wages import calculate_absence_deduction
from app.database.database import AbsenceType

HOURLY = 200.0
HOURS = 2.0


def test_both_late_types_exist():
    assert AbsenceType.LATE_PAID
    assert AbsenceType.LATE_UNPAID


def test_unpaid_late_deducts_like_leave():
    late = calculate_absence_deduction(HOURLY * 173, "LATE_UNPAID", HOURS)
    leave = calculate_absence_deduction(HOURLY * 173, "LEAVE", HOURS)
    assert late == leave
    assert late > 0


def test_paid_late_costs_nothing_like_off():
    assert calculate_absence_deduction(HOURLY * 173, "LATE_PAID", HOURS) == 0.0
    assert calculate_absence_deduction(HOURLY * 173, "LATE_PAID", HOURS) == calculate_absence_deduction(
        HOURLY * 173, "OFF", HOURS
    )


@pytest.mark.parametrize(
    "absence_type,expected",
    [(AbsenceType.LATE_UNPAID, "LEAVE"), (AbsenceType.LATE_PAID, "OFF")],
)
def test_a_full_day_of_either_falls_back_to_its_twin_shift_code(absence_type, expected):
    """Late arrival is inherently partial, but the code must still resolve if the
    time is left blank."""
    from app.core.schedule.period import _absence_shift_code

    assert _absence_shift_code(absence_type) == expected


@pytest.mark.parametrize("lang", ["sv", "en"])
def test_both_types_are_named_in_both_languages(lang):
    from app.core.translations import TRANSLATIONS

    labels = TRANSLATIONS[lang]["absence_labels"]
    assert "LATE_PAID" in labels
    assert "LATE_UNPAID" in labels
    assert TRANSLATIONS[lang]["day_absence_late_paid"]
    assert TRANSLATIONS[lang]["day_absence_late_unpaid"]


@pytest.fixture
def seeded(test_db, monkeypatch):
    """A user with a rotation, so an absence has a shift to be absent from."""
    from sqlalchemy.orm import sessionmaker

    import app.database.database as db_module
    from app.core.schedule import clear_schedule_cache
    from app.database.database import RotationEra
    from tests.conftest import _ROTATION_ERA_PATTERN

    monkeypatch.setattr(db_module, "SessionLocal", sessionmaker(bind=test_db.get_bind()))
    clear_schedule_cache()
    test_db.query(RotationEra).delete()
    test_db.add(
        RotationEra(
            start_date=datetime.date(2026, 1, 2),
            end_date=None,
            rotation_length=10,
            weeks_pattern=_ROTATION_ERA_PATTERN,
        )
    )
    test_db.commit()
    yield test_db
    clear_schedule_cache()


def test_an_unpaid_late_hour_reaches_the_month_as_leave(seeded, test_user):
    """The payslip reports one unpaid-absence row, so a late hour has to land in it.

    2026-03-02 is N2 (14:00-22:30) in the seeded rotation, so 16:00 is two hours late.
    """
    from app.core.schedule.wages import get_absence_deductions_for_month
    from app.database.database import Absence

    date = datetime.date(2026, 3, 2)
    seeded.add(
        Absence(
            user_id=test_user.id,
            date=date,
            absence_type=AbsenceType.LATE_UNPAID,
            arrived_at="16:00",
        )
    )
    seeded.commit()

    info = get_absence_deductions_for_month(seeded, test_user.id, 2026, 3, 30000)
    assert info["leave_hours"] > 0, info
    assert info["total_deduction"] > 0, info


def test_a_paid_late_hour_costs_nothing_in_the_month(seeded, test_user):
    from app.core.schedule.wages import get_absence_deductions_for_month
    from app.database.database import Absence

    seeded.add(
        Absence(
            user_id=test_user.id,
            date=datetime.date(2026, 3, 3),
            absence_type=AbsenceType.LATE_PAID,
            arrived_at="16:00",
        )
    )
    seeded.commit()

    info = get_absence_deductions_for_month(seeded, test_user.id, 2026, 3, 30000)
    assert info["total_deduction"] == 0.0, info


def test_the_absence_form_offers_both(env_absence_form):
    html = env_absence_form
    assert 'value="LATE_UNPAID"' in html
    assert 'value="LATE_PAID"' in html


@pytest.fixture
def env_absence_form(test_db, test_client, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    import app.database.database as db_module
    from app.auth.auth import create_access_token
    from app.core.schedule import clear_schedule_cache
    from app.database.database import PersonHistory, RotationEra, User, UserRole, WageType
    from tests.conftest import _ROTATION_ERA_PATTERN

    monkeypatch.setattr(db_module, "SessionLocal", sessionmaker(bind=test_db.get_bind()))
    clear_schedule_cache()
    test_db.query(RotationEra).delete()
    test_db.add(
        RotationEra(
            start_date=datetime.date(2026, 1, 2),
            end_date=None,
            rotation_length=10,
            weeks_pattern=_ROTATION_ERA_PATTERN,
        )
    )
    test_db.add(
        User(
            id=1,
            username="u1",
            password_hash="x",
            name="User 1",
            role=UserRole.USER,
            wage=30000,
            wage_type=WageType.MONTHLY,
            vacation={},
            must_change_password=0,
            is_active=1,
            person_id=1,
        )
    )
    test_db.add(
        PersonHistory(
            user_id=1,
            person_id=1,
            name="User 1",
            username="u1",
            is_active=1,
            effective_from=datetime.date(2026, 1, 2),
            effective_to=None,
            created_by=1,
        )
    )
    test_db.commit()
    test_client.cookies.set("access_token", create_access_token(data={"sub": "1"}))
    yield test_client.get("/day/1/2026/3/2").text
    clear_schedule_cache()
