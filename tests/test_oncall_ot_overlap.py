"""On-call is reduced by the union of every overtime interval that overlaps it."""

from datetime import date, time

import pytest

from app.core.schedule import clear_schedule_cache, generate_period_data
from app.database.database import (
    OnCallOverride,
    OnCallOverrideType,
    OvertimeShift,
    RotationEra,
    User,
    UserRole,
    WageType,
)
from tests.conftest import _ROTATION_ERA_PATTERN

SALARY = 35000
OC_DAY = date(2026, 8, 31)  # Monday
PREV_DAY = date(2026, 8, 30)


@pytest.fixture()
def env(test_db):
    clear_schedule_cache()
    test_db.add(
        RotationEra(
            start_date=date(2026, 1, 2),
            end_date=None,
            rotation_length=10,
            weeks_pattern=_ROTATION_ERA_PATTERN,
        )
    )
    user = User(
        id=1,
        username="ocuser",
        password_hash="x",
        name="OC User",
        role=UserRole.USER,
        wage=SALARY,
        wage_type=WageType.MONTHLY,
        person_id=1,
        vacation={},
        must_change_password=0,
        is_active=1,
    )
    test_db.add(user)
    test_db.add(OnCallOverride(user_id=1, date=OC_DAY, override_type=OnCallOverrideType.ADD))
    test_db.commit()
    yield test_db, user
    clear_schedule_cache()


def _ot(session, day, start, end, hours, side="full"):
    session.add(
        OvertimeShift(
            user_id=1,
            date=day,
            start_time=time.fromisoformat(start),
            end_time=time.fromisoformat(end),
            hours=hours,
            ot_pay=0.0,
            kind="ot",
            side=side,
        )
    )
    session.commit()


def _oc_day(session):
    days = generate_period_data(OC_DAY, OC_DAY, person_id=1, session=session)
    day = next(d for d in days if d["date"] == OC_DAY)
    assert day["shift"] is not None
    return day


@pytest.mark.parametrize("profile", ["ica", "nexer"])
def test_previous_and_same_day_night_overtime_both_deduct(env, profile):
    session, user = env
    user.employer_profile = profile
    _ot(session, PREV_DAY, "22:00", "06:30", 8.5)
    _ot(session, OC_DAY, "22:00", "06:30", 8.5)
    details = _oc_day(session)["oncall_details"]
    assert details["total_hours"] == 15.5
    assert [(p["start"].time(), p["end"].time()) for p in details["periods"]] == [(time(6, 30), time(22, 0))]
    if profile == "nexer":
        assert round(details["total_pay"], 2) == round(15.5 * SALARY / 1400, 2)


def test_same_day_row_only(env):
    session, _ = env
    _ot(session, OC_DAY, "22:00", "06:30", 8.5)
    assert _oc_day(session)["oncall_details"]["total_hours"] == 22


def test_previous_day_spill_only(env):
    session, _ = env
    _ot(session, PREV_DAY, "22:00", "06:30", 8.5)
    assert _oc_day(session)["oncall_details"]["total_hours"] == 17.5


def test_overlapping_same_day_rows_not_double_deducted(env):
    session, _ = env
    _ot(session, OC_DAY, "10:00", "14:00", 4.0, side="before")
    _ot(session, OC_DAY, "12:00", "16:00", 4.0, side="after")
    details = _oc_day(session)["oncall_details"]
    assert details["total_hours"] == 18
    assert sum(b["hours"] for b in details["breakdown"].values()) == 18


def test_ot_rows_for_display_unchanged(env):
    session, _ = env
    _ot(session, PREV_DAY, "22:00", "06:30", 8.5)
    _ot(session, OC_DAY, "22:00", "06:30", 8.5)
    day = _oc_day(session)
    assert day["ot_hours"] == 8.5


def test_substitute_oncall_deducts_previous_and_same_day_overtime(test_client, test_db, admin_user):
    from app.auth.auth import create_access_token
    from app.core.schedule.period import build_substitute_month_summaries
    from app.database.database import Substitute

    test_client.cookies.set("access_token", create_access_token(data={"sub": str(admin_user.id)}))
    test_client.post("/admin/substitutes/create", data={"name": "Nattvik"})
    sub = test_db.query(Substitute).filter(Substitute.name == "Nattvik").one()
    test_client.post(
        f"/admin/substitutes/{sub.id}/save",
        data={"year": "2026", "month": "8", "shift_2026-08-31": "OC"},
    )
    for day in ("2026-08-30", "2026-08-31"):
        test_client.post(
            f"/admin/substitutes/{sub.id}/overtime/add",
            data={"date": day, "start_time": "22:00", "end_time": "06:30", "hours": "8.5"},
        )
    summary = next(s for s in build_substitute_month_summaries(2026, 8, test_db) if s["substitute_id"] == sub.id)
    assert summary["oncall_hours"] == 15.5
