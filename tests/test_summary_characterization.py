"""Characterization tests for summarize_month_for_person (audit item A1).

These pin the CURRENT behaviour of the monthly pay summary so the planned breakup of the
oversized payroll functions can be done safely: any refactor that changes a computed total
will fail here. The golden values were captured from the committed rotation/OB/tax data for
rotation position 1, March 2026, a monthly-wage user of 30000 with tax table 33.

If legitimate data or logic changes move these numbers, update the constants deliberately.
"""

import datetime

import pytest

from app.core.schedule.core import determine_shift_for_date
from app.core.schedule.summary import summarize_month_for_person
from app.database.database import Absence, AbsenceType, User, UserRole, WageType

YEAR = 2026
MONTH = 3
PERSON_ID = 1
WAGE = 30000

EXPECTED_KEYS = {
    "year",
    "month",
    "person_id",
    "person_name",
    "total_hours",
    "num_shifts",
    "ob_hours",
    "ob_pay",
    "oncall_pay",
    "oncall_hours",
    "ot_pay",
    "absence_deduction",
    "absence_hours",
    "brutto_pay",
    "netto_pay",
    "sick_days",
    "sick_hours",
    "vab_days",
    "leave_days",
    "parental_days",
    "days",
}


@pytest.fixture
def char_user(test_db):
    user = User(
        id=1,
        username="charuser",
        password_hash="x",
        name="Characterization",
        role=UserRole.USER,
        wage=WAGE,
        wage_type=WageType.MONTHLY,
        person_id=PERSON_ID,
        tax_table="33",
        vacation={},
        must_change_password=0,
    )
    test_db.add(user)
    test_db.commit()
    return user


def test_summary_golden_master(test_db, char_user):
    s = summarize_month_for_person(YEAR, MONTH, PERSON_ID, session=test_db, wage_user_id=char_user.id)

    # Captured golden values for this fixed scenario.
    assert s["total_hours"] == 144.5
    assert s["num_shifts"] == 17
    assert s["oncall_pay"] == 6082.0
    assert s["ot_pay"] == 0.0
    assert s["brutto_pay"] == 44207.0
    assert s["netto_pay"] == 34353.0
    assert s["absence_deduction"] == 0.0
    assert s["sick_days"] == 0


def test_summary_structure_and_invariants(test_db, char_user):
    s = summarize_month_for_person(YEAR, MONTH, PERSON_ID, session=test_db, wage_user_id=char_user.id)

    assert EXPECTED_KEYS.issubset(s.keys())
    assert isinstance(s["ob_pay"], dict)
    assert isinstance(s["ob_hours"], dict)
    assert s["total_hours"] >= 0
    assert s["num_shifts"] >= 0
    # Net is gross minus (non-negative) tax, and never above gross.
    assert s["netto_pay"] <= s["brutto_pay"]


def test_summary_reflects_a_sick_day(test_db, char_user):
    baseline = summarize_month_for_person(YEAR, MONTH, PERSON_ID, session=test_db, wage_user_id=char_user.id)

    # Place a sick day on the first scheduled work day of the month.
    work_day = next(
        d
        for d in (datetime.date(YEAR, MONTH, day) for day in range(1, 29))
        if (sh := determine_shift_for_date(d, PERSON_ID)[0]) and sh.code != "OFF"
    )
    test_db.add(Absence(user_id=char_user.id, date=work_day, absence_type=AbsenceType.SICK))
    test_db.commit()

    s = summarize_month_for_person(YEAR, MONTH, PERSON_ID, session=test_db, wage_user_id=char_user.id)

    assert s["sick_days"] == 1
    assert s["absence_deduction"] > 0
    # A sick day reduces gross relative to a fully worked month.
    assert s["brutto_pay"] < baseline["brutto_pay"]
