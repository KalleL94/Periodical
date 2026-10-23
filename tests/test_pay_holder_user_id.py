"""On-call and overtime pay read the wage of the day's holder, not of the user whose id is the position.

Prod has a holder whose user id differs from their rotation position. The wage
lookups must follow the holder, the same way the employer profile does.
"""

import datetime
from datetime import date

import pytest

from app.core.schedule import clear_schedule_cache, generate_period_data
from app.database.database import (
    DayPayOverride,
    OnCallOverride,
    OnCallOverrideType,
    OvertimeShift,
    PersonHistory,
    RotationEra,
    User,
    UserRole,
    WageType,
)
from tests.conftest import _ROTATION_ERA_PATTERN

POSITION = 3
HOLDER_ID = 11
SALARY = 35000
DECOY_SALARY = 20000
FRIDAY = date(2026, 5, 8)
SATURDAY = date(2026, 5, 9)


def _user(uid, username, wage, person_id, profile, is_active=1):
    return User(
        id=uid,
        username=username,
        password_hash="x",
        name=username,
        role=UserRole.USER,
        wage=wage,
        wage_type=WageType.MONTHLY,
        person_id=person_id,
        vacation={},
        must_change_password=0,
        is_active=is_active,
        employer_profile=profile,
    )


@pytest.fixture(params=["nexer", "ica"])
def holder_env(request, test_db):
    clear_schedule_cache()
    profile = request.param
    test_db.add(
        RotationEra(
            start_date=datetime.date(2026, 1, 2),
            end_date=None,
            rotation_length=10,
            weeks_pattern=_ROTATION_ERA_PATTERN,
        )
    )
    # A user whose id equals the position but who does not hold it: a wage read
    # keyed on the position would find this row.
    test_db.add(_user(POSITION, "decoy", DECOY_SALARY, None, "ica", is_active=0))
    test_db.add(_user(HOLDER_ID, "holder", SALARY, POSITION, profile))
    test_db.add(
        PersonHistory(
            user_id=HOLDER_ID,
            person_id=POSITION,
            name="holder",
            username="holder",
            is_active=1,
            effective_from=date(2026, 1, 1),
            effective_to=None,
        )
    )
    for day in (FRIDAY, SATURDAY):
        test_db.add(OnCallOverride(user_id=HOLDER_ID, date=day, override_type=OnCallOverrideType.ADD))
    test_db.add(
        OvertimeShift(
            user_id=HOLDER_ID,
            date=FRIDAY,
            start_time=datetime.time(10, 0),
            end_time=datetime.time(14, 0),
            hours=4.0,
            ot_pay=0.0,
            kind="ot",
            side="full",
        )
    )
    code = "NX_SPECIAL" if profile == "nexer" else "OC_WEEKDAY"
    test_db.add(DayPayOverride(user_id=HOLDER_ID, date=SATURDAY, oncall_hours_override={code: 2.0}))
    test_db.commit()
    yield test_db, profile
    clear_schedule_cache()


def _days(session):
    days = generate_period_data(FRIDAY, SATURDAY, person_id=POSITION, session=session)
    return {d["date"]: d for d in days}


def test_overtime_uses_holder_wage(holder_env):
    session, profile = holder_env
    friday = _days(session)[FRIDAY]
    # Friday 10-14 on a weekday: Nexer prices it at salary / 94, ICA at salary / 72.
    expected = 4 * SALARY / 94 if profile == "nexer" else 4 * SALARY / 72
    assert round(friday["ot_pay"], 2) == round(expected, 2)


def test_oncall_uses_holder_wage(holder_env):
    session, profile = holder_env
    friday = _days(session)[FRIDAY]
    breakdown = friday["oncall_details"]["breakdown"]
    if profile == "nexer":
        assert {c: b["hours"] for c, b in breakdown.items()} == {"NX_WEEKDAY": 14, "NX_NIGHT": 6}
        assert round(friday["oncall_pay"], 2) == round(14 * SALARY / 1400 + 6 * SALARY / 900, 2)
    else:
        # ICA on-call is a fixed kr/h table, so the wage does not enter it.
        assert friday["oncall_pay"] > 0


def test_oncall_hour_override_uses_holder_wage(holder_env):
    session, profile = holder_env
    saturday = _days(session)[SATURDAY]
    if profile == "nexer":
        assert saturday["oncall_pay"] == round(2 * SALARY / 350, 2)
    else:
        assert set(saturday["oncall_details"]["breakdown"]) == {"OC_WEEKDAY"}
