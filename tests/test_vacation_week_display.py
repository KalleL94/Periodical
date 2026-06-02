"""Week-based vacation/parental leave should only show SEM/LEAVE on scheduled (non-OFF) days."""

from app.core.schedule import build_week_data, clear_schedule_cache
from app.core.schedule.core import determine_shift_for_date


def _set_week_vacation(test_db, field, week):
    from app.database.database import User

    user = test_db.query(User).filter(User.id == 1).first()
    user.person_id = 1
    setattr(user, field, {"2026": [week]})
    test_db.commit()
    clear_schedule_cache()


def test_week_vacation_only_on_scheduled_days(test_db, test_user):
    week = 28
    _set_week_vacation(test_db, "vacation", week)

    days = build_week_data(2026, week, person_id=1, session=test_db)

    checked_off = checked_work = False
    for day in days:
        rotation, _ = determine_shift_for_date(day["date"], start_week=1)
        built = day["shift"].code
        if rotation and rotation.code == "OFF":
            assert built == "OFF", f"{day['date']} OFF in rotation should stay OFF, got {built}"
            checked_off = True
        else:
            assert built == "SEM", f"{day['date']} scheduled should show SEM, got {built}"
            checked_work = True

    assert checked_off and checked_work, "expected the week to contain both OFF and scheduled days"


def test_week_parental_only_on_scheduled_days(test_db, test_user):
    week = 28
    _set_week_vacation(test_db, "parental_leave", week)

    days = build_week_data(2026, week, person_id=1, session=test_db)

    checked_off = checked_work = False
    for day in days:
        rotation, _ = determine_shift_for_date(day["date"], start_week=1)
        built = day["shift"].code
        if rotation and rotation.code == "OFF":
            assert built == "OFF", f"{day['date']} OFF in rotation should stay OFF, got {built}"
            checked_off = True
        else:
            assert built == "LEAVE", f"{day['date']} scheduled should show LEAVE, got {built}"
            checked_work = True

    assert checked_off and checked_work, "expected the week to contain both OFF and scheduled days"
