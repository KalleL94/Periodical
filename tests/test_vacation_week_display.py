"""Week-based vacation/parental leave should only show SEM/LEAVE on scheduled (non-OFF) days."""

from app.core.schedule import build_week_data, clear_schedule_cache
from app.core.schedule.core import determine_shift_for_date
from app.database.database import User


def _set_week_leave(session, field, week):
    user = session.query(User).filter(User.id == 1).first()
    setattr(user, field, {"2026": [week]})
    session.commit()
    clear_schedule_cache()


def _assert_only_scheduled(session, leave_code):
    week = 28
    days = build_week_data(2026, week, person_id=1, session=session)

    checked_off = checked_work = False
    for day in days:
        rotation, _ = determine_shift_for_date(day["date"], start_week=1)
        built = day["shift"].code
        if rotation and rotation.code == "OFF":
            assert built == "OFF", f"{day['date']} OFF in rotation should stay OFF, got {built}"
            checked_off = True
        else:
            assert built == leave_code, f"{day['date']} scheduled should show {leave_code}, got {built}"
            checked_work = True

    assert checked_off and checked_work, "expected the week to contain both OFF and scheduled days"


def test_week_vacation_only_on_scheduled_days(rotation_session):
    _set_week_leave(rotation_session, "vacation", 28)
    _assert_only_scheduled(rotation_session, "SEM")


def test_week_parental_only_on_scheduled_days(rotation_session):
    _set_week_leave(rotation_session, "parental_leave", 28)
    _assert_only_scheduled(rotation_session, "LEAVE")
