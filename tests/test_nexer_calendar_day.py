"""Nexer: OB and overtime count on the calendar day they are worked, also in month totals."""

import datetime
from datetime import date

import pytest

from app.core.schedule import clear_schedule_cache
from app.core.schedule.ob import calculate_ob_hours_by_day, get_combined_rules_for_year
from app.core.schedule.summary import summarize_month_for_person
from app.database.database import OvertimeShift, PersonHistory, RotationEra, ShiftOverride, User
from tests.conftest import _ROTATION_ERA_PATTERN
from tests.test_employer_profile import _login

SALARY = 35000


@pytest.fixture()
def env(test_db, test_user):
    clear_schedule_cache()
    test_db.add(
        RotationEra(
            start_date=date(2026, 1, 2),
            end_date=None,
            rotation_length=10,
            weeks_pattern=_ROTATION_ERA_PATTERN,
        )
    )
    test_user.person_id = 1
    test_user.employer_profile = "nexer"
    # Keep the other month boundaries free of night shifts, so only 31 Aug crosses one.
    for d in (date(2026, 7, 31), date(2026, 9, 30)):
        test_db.add(ShiftOverride(user_id=1, date=d, shift_code="OFF"))
    test_db.commit()
    yield test_db, test_user
    clear_schedule_cache()


def _add_night_ot(session, on=date(2026, 8, 31)):
    session.add(
        OvertimeShift(
            user_id=1,
            date=on,
            start_time=datetime.time(22, 0),
            end_time=datetime.time(6, 30),
            hours=8.5,
            ot_pay=0.0,
            kind="ot",
            side="full",
        )
    )
    session.commit()
    clear_schedule_cache()


def _month(session, month, profile=None, **kw):
    if profile:
        session.query(User).filter_by(id=1).update({"employer_profile": profile})
        session.commit()
    clear_schedule_cache()
    return summarize_month_for_person(2026, month, 1, session=session, **kw)


def test_nexer_overtime_splits_at_month_boundary(env):
    session, _ = env
    session.add(ShiftOverride(user_id=1, date=date(2026, 8, 31), shift_code="OFF"))
    session.commit()
    aug_before = _month(session, 8)["brutto_pay"]
    sep_before = _month(session, 9)["brutto_pay"]
    _add_night_ot(session)

    aug, sep = _month(session, 8), _month(session, 9)
    aug_pay = 2 * SALARY / 72
    sep_pay = 6 * SALARY / 72 + 0.5 * SALARY / 94  # Tue 1 Sep, 06:00-06:30 is weekday daytime
    assert aug["ot_hours"] == pytest.approx(2.0)
    assert aug["ot_pay"] == pytest.approx(aug_pay)
    assert sep["ot_hours"] == pytest.approx(6.5)
    assert sep["ot_pay"] == pytest.approx(sep_pay)
    assert aug["brutto_pay"] - aug_before == pytest.approx(aug_pay)
    assert sep["brutto_pay"] - sep_before == pytest.approx(sep_pay)
    assert aug["payslip"].by_key()["ot"].amount == pytest.approx(aug_pay)
    assert sep["payslip"].by_key()["ot"].amount == pytest.approx(sep_pay)
    assert sep["payslip"].by_key()["ot"].qty == pytest.approx(6.5)


def test_ica_overtime_stays_on_start_date(env):
    session, _ = env
    _add_night_ot(session)
    aug, sep = _month(session, 8, "ica"), _month(session, 9)
    assert aug["ot_hours"] == pytest.approx(8.5)
    assert aug["ot_pay"] == pytest.approx(8.5 * SALARY / 72)
    assert sep["ot_hours"] == 0
    assert sep["ot_pay"] == 0


def test_nexer_night_shift_ob_moves_to_next_month(env):
    session, _ = env
    session.add(ShiftOverride(user_id=1, date=date(2026, 8, 31), shift_code="N3"))
    session.commit()
    after_midnight = calculate_ob_hours_by_day(
        datetime.datetime(2026, 8, 31, 22),
        datetime.datetime(2026, 9, 1, 6, 30),
        get_combined_rules_for_year(2026),
    )[date(2026, 9, 1)]
    assert sum(after_midnight.values()) > 0

    ica_aug, ica_sep = _month(session, 8, "ica"), _month(session, 9)
    nx_aug, nx_sep = _month(session, 8, "nexer"), _month(session, 9)
    for code, hrs in after_midnight.items():
        assert nx_aug["ob_hours"][code] == pytest.approx(ica_aug["ob_hours"][code] - hrs)
        assert nx_sep["ob_hours"][code] == pytest.approx(ica_sep["ob_hours"][code] + hrs)
    moved = sum(ica_aug["ob_pay"].values()) - sum(nx_aug["ob_pay"].values())
    assert moved > 0
    assert sum(nx_sep["ob_pay"].values()) - sum(ica_sep["ob_pay"].values()) == pytest.approx(moved)


def test_nexer_month_total_is_sum_of_calendar_days(env):
    session, _ = env
    session.add(ShiftOverride(user_id=1, date=date(2026, 8, 31), shift_code="N3"))
    session.commit()
    _add_night_ot(session, on=date(2026, 9, 30))  # its after-midnight part leaves for October
    for month in (8, 9):
        m = _month(session, month)
        for code, hrs in m["ob_hours"].items():
            assert sum(d["ob_hours_calendar_day"].get(code, 0.0) for d in m["days"]) == pytest.approx(hrs)
        assert sum(d["ot_hours_calendar_day"] for d in m["days"]) == pytest.approx(m["ot_hours"])
        assert m["ot_hours"] == pytest.approx({8: 0.0, 9: 2.0}[month])
        slip = m["payslip"].by_key()
        for code, pay in m["ob_pay"].items():
            assert (slip[code].amount if code in slip else 0.0) == pytest.approx(pay)


def test_predecessor_night_does_not_reach_successor(env):
    session, nexer_a = env
    _add_night_ot(session)
    session.add(
        User(
            id=2,
            username="b",
            is_active=1,
            password_hash="x",
            name="B",
            wage=SALARY,
            vacation={},
            employer_profile="nexer",
        )
    )
    session.add(
        PersonHistory(
            user_id=1,
            person_id=1,
            name="A",
            username="testuser",
            is_active=1,
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 8, 31),
        )
    )
    session.add(
        PersonHistory(
            user_id=2,
            person_id=1,
            name="B",
            username="b",
            is_active=1,
            effective_from=date(2026, 9, 1),
            effective_to=None,
        )
    )
    session.commit()
    sep = _month(session, 9, wage_user_id=2)
    assert sep["ot_hours"] == 0


def test_nexer_breakdown_opens_in_calendar_day_mode(test_client, env):
    session, _ = env
    _add_night_ot(session)
    _login(test_client, "testuser", "testpass123")
    html = test_client.get("/month/1?year=2026&month=9").text
    assert 'id="breakdown-section" class="ob-by-day"' in html
    # The footer follows the month totals: 1 Sep carries the 6.5 h after midnight.
    assert "<strong>6.5</strong>" in html
    session.query(User).filter_by(id=1).update({"employer_profile": "ica"})
    session.commit()
    clear_schedule_cache()
    html = test_client.get("/month/1?year=2026&month=9").text
    assert 'id="breakdown-section" class="ob-by-shift"' in html


def test_year_months_follow_calendar_days(env):
    from app.core.schedule.summary import summarize_year_for_person

    session, _ = env
    _add_night_ot(session)
    clear_schedule_cache()
    year = summarize_year_for_person(2026, 1, session=session, wage_user_id=1, employment_user_id=1)
    by_month = {(m["year"], m["month"]): m for m in year["months"]}
    assert by_month[(2026, 8)]["ot_hours"] == pytest.approx(2.0)
    assert by_month[(2026, 9)]["ot_hours"] == pytest.approx(6.5)
    assert year["year_summary"]["total_ot"] == pytest.approx(sum(m["ot_pay"] for m in year["months"]))


def test_nexer_worked_hours_follow_calendar_day(env):
    session, _ = env
    session.add(ShiftOverride(user_id=1, date=date(2026, 8, 31), shift_code="N3"))
    session.commit()
    ica_aug, ica_sep = _month(session, 8, "ica"), _month(session, 9)
    nx_aug, nx_sep = _month(session, 8, "nexer"), _month(session, 9)
    # The 8.5 h night: ica keeps it on 31 Aug, nexer gives August 2 h and September 6.5 h.
    assert nx_aug["total_hours"] == pytest.approx(ica_aug["total_hours"] - 6.5)
    assert nx_sep["total_hours"] == pytest.approx(ica_sep["total_hours"] + 6.5)
    assert nx_aug["num_shifts"] == ica_aug["num_shifts"]
    assert nx_sep["num_shifts"] == ica_sep["num_shifts"]


def test_hourly_nexer_base_pay_follows_calendar_day(env):
    from app.database.database import WageType

    session, user = env
    user.wage_type = WageType.HOURLY
    user.wage = 200
    session.add(ShiftOverride(user_id=1, date=date(2026, 8, 31), shift_code="N3"))
    session.commit()
    ica_sep, nx_sep = _month(session, 9, "ica"), _month(session, 9, "nexer")
    assert nx_sep["payslip"].by_key()["norm"].qty == pytest.approx(ica_sep["payslip"].by_key()["norm"].qty + 6.5)
    assert nx_sep["payslip"].by_key()["norm"].amount == pytest.approx(
        ica_sep["payslip"].by_key()["norm"].amount + 6.5 * 200
    )


def _week_hours(client, week):
    import re

    html = client.get(f"/?week={week}&wyear=2026").text
    return float(re.search(r'class="summary-value">([\d.]+)h<', html).group(1))


def test_nexer_week_summary_follows_calendar_day(test_client, env):
    session, _ = env
    # Sunday 6 Sep night into Monday 7 Sep; the neighbouring Sundays stay free.
    session.add(ShiftOverride(user_id=1, date=date(2026, 9, 6), shift_code="N3"))
    for d in (date(2026, 8, 30), date(2026, 9, 13)):
        session.add(ShiftOverride(user_id=1, date=d, shift_code="OFF"))
    session.commit()
    _login(test_client, "testuser", "testpass123")
    hours = {}
    for profile in ("ica", "nexer"):
        session.query(User).filter_by(id=1).update({"employer_profile": profile})
        session.commit()
        clear_schedule_cache()
        hours[profile] = (_week_hours(test_client, 36), _week_hours(test_client, 37))
    assert hours["nexer"][0] == pytest.approx(hours["ica"][0] - 6.5)
    assert hours["nexer"][1] == pytest.approx(hours["ica"][1] + 6.5)


@pytest.mark.parametrize("night", ["N3", "OT"])
def test_employment_from_the_1st_ignores_the_night_before(env, night):
    """A night on 31 Aug belongs to whoever worked it: employed from 1 Sep, it is not yours."""
    from app.core.schedule.summary import build_calendar_grid_for_month

    session, _ = env
    if night == "N3":
        session.add(ShiftOverride(user_id=1, date=date(2026, 8, 31), shift_code="N3"))
        session.commit()
    else:
        _add_night_ot(session)

    def sep(profile):
        session.query(User).filter_by(id=1).update({"employer_profile": profile})
        session.commit()
        clear_schedule_cache()
        grid = build_calendar_grid_for_month(
            2026, 9, 1, session=session, employment_start=date(2026, 9, 1), wage_user_id=1
        )
        return grid["summary"]

    ica, nexer = sep("ica"), sep("nexer")
    assert nexer["total_hours"] == pytest.approx(ica["total_hours"])
    assert nexer["ob_hours"] == pytest.approx(ica["ob_hours"])
    assert nexer["ot_hours"] == pytest.approx(ica["ot_hours"]) == 0


def test_admin_month_report_takes_holder_night_before(env):
    """The holder's user id differs from the position: the night before still counts."""
    from app.core.schedule.summary import build_month_report

    session, user = env
    user.person_id = 2
    user.employer_profile = "ica"
    session.add(
        User(id=11, username="h", is_active=1, password_hash="x", name="H", wage=SALARY, vacation={}, person_id=1)
    )
    session.add(
        PersonHistory(user_id=11, person_id=1, name="H", username="h", is_active=1, effective_from=date(2026, 1, 1))
    )
    session.add(ShiftOverride(user_id=11, date=date(2026, 8, 31), shift_code="N3"))
    session.add(ShiftOverride(user_id=11, date=date(2026, 9, 30), shift_code="OFF"))
    session.commit()

    def sep_hours(profile):
        session.query(User).filter_by(id=11).update({"employer_profile": profile})
        session.commit()
        clear_schedule_cache()
        return next(r for r in build_month_report(2026, 9, session) if r["holder_user_id"] == 11)["total_hours"]

    assert sep_hours("nexer") == pytest.approx(sep_hours("ica") + 6.5)


def test_nexer_disjoint_extra_time_splits_by_its_own_interval(env):
    """Shift 06:00-14:30 plus extra time 23:00-01:00: the gap between them is not worked."""
    session, _ = env
    session.add(ShiftOverride(user_id=1, date=date(2026, 8, 31), shift_code="N1"))
    session.add(
        OvertimeShift(
            user_id=1,
            date=date(2026, 8, 31),
            start_time=datetime.time(23, 0),
            end_time=datetime.time(1, 0),
            hours=2.0,
            ot_pay=0.0,
            kind="extra",
            side="after",
        )
    )
    session.commit()
    ica_aug, ica_sep = _month(session, 8, "ica"), _month(session, 9)
    nx_aug, nx_sep = _month(session, 8, "nexer"), _month(session, 9)
    last = next(d for d in nx_aug["days"] if d["date"] == date(2026, 8, 31))
    assert last["calendar_day_pay"]["worked_hours"] == pytest.approx(8.5 + 1)
    assert nx_aug["total_hours"] == pytest.approx(ica_aug["total_hours"] - 1)
    assert nx_sep["total_hours"] == pytest.approx(ica_sep["total_hours"] + 1)
    ica_last = next(d for d in ica_aug["days"] if d["date"] == date(2026, 8, 31))
    assert ica_last["hours"] == pytest.approx(10.5)
