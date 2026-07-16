"""Substitute-to-user account link (issue #290): helper lookups, pay
integration for pre-employment substitute shifts, and double-count guards."""

import datetime

import pytest
from sqlalchemy.orm import sessionmaker

import app.database.database as db_module
from app.core.schedule import (
    calculate_ob_pay,
    calculate_shift_hours,
    clear_schedule_cache,
    get_combined_rules_for_year,
)
from app.core.schedule.period import get_linked_substitutes_for_user
from app.core.schedule.summary import build_calendar_grid_for_month, build_month_report, summarize_month_for_person
from app.database.database import (
    Absence,
    AbsenceType,
    OvertimeShift,
    PersonHistory,
    RotationEra,
    ShiftOverride,
    Substitute,
    SubstituteShift,
    User,
    UserRole,
    WageType,
)
from tests.conftest import _ROTATION_ERA_PATTERN

_SUB_WAGE = 200
_MONTHLY_EQUIV = int(_SUB_WAGE * 173.33)


@pytest.fixture()
def env(test_db, test_client, monkeypatch):
    """Bind the global SessionLocal to the test DB and seed the rotation era
    (same technique as tests/test_day_view_consistency.py)."""
    engine = test_db.get_bind()
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(db_module, "SessionLocal", session_local)
    clear_schedule_cache()
    test_db.add(
        RotationEra(
            start_date=datetime.date(2026, 1, 2),
            end_date=None,
            rotation_length=10,
            weeks_pattern=_ROTATION_ERA_PATTERN,
        )
    )
    test_db.commit()
    yield test_client, test_db
    clear_schedule_cache()


def _make_user(session, uid, person_id, wage=30000, wage_type=WageType.MONTHLY):
    user = User(
        id=uid,
        username=f"u{uid}",
        password_hash="x",
        name=f"User {uid}",
        role=UserRole.USER,
        wage=wage,
        wage_type=wage_type,
        vacation={},
        must_change_password=0,
        is_active=1,
        person_id=person_id,
    )
    session.add(user)
    session.commit()
    return user


def test_get_linked_substitutes_for_user(env):
    _, session = env
    _make_user(session, 1, 1)
    _make_user(session, 2, 2)
    linked_active = Substitute(name="Sub A", is_active=1, user_id=1, hourly_wage=180)
    linked_archived = Substitute(name="Sub B", is_active=0, user_id=1, hourly_wage=170)
    unlinked = Substitute(name="Sub C", is_active=1)
    other_user = Substitute(name="Sub D", is_active=1, user_id=2)
    session.add_all([linked_active, linked_archived, unlinked, other_user])
    session.commit()

    result = get_linked_substitutes_for_user(session, 1)
    names = sorted(s.name for s in result)
    # Archived substitutes stay included: their history belongs to the user.
    assert names == ["Sub A", "Sub B"]

    assert get_linked_substitutes_for_user(session, 3) == []
    assert get_linked_substitutes_for_user(session, None) == []
    assert get_linked_substitutes_for_user(None, 1) == []


# ---------------------------------------------------------------------------
# Pay integration (issue #290, plan step 4)
# ---------------------------------------------------------------------------


def _seed_linked_user(session, uid=1, emp_start=datetime.date(2026, 6, 1), wage=30000, wage_type=WageType.MONTHLY):
    """User on position uid employed from emp_start, plus a linked substitute."""
    _make_user(session, uid, uid, wage=wage, wage_type=wage_type)
    session.add(
        PersonHistory(
            user_id=uid,
            person_id=uid,
            name=f"User {uid}",
            username=f"u{uid}",
            is_active=1,
            effective_from=emp_start,
        )
    )
    sub = Substitute(name="Sommarvikarie", is_active=1, user_id=uid, hourly_wage=_SUB_WAGE)
    session.add(sub)
    session.commit()
    return sub


def _summary_day(summary, d):
    return next(x for x in summary["days"] if x["date"] == d)


def test_substitute_ob_priced_like_hourly_user(env):
    """A substitute N2 day must yield the same OB pay as an HOURLY user with
    the same hourly wage working the same shift on the same date."""
    _, session = env
    day = datetime.date(2026, 3, 10)
    sub = _seed_linked_user(session, uid=1)
    session.add(SubstituteShift(substitute_id=sub.id, date=day, shift_code="N2"))

    # Hourly-wage user on position 2 with the same N2 shift the same date
    _make_user(session, 2, 2, wage=_SUB_WAGE, wage_type=WageType.HOURLY)
    session.add(ShiftOverride(user_id=2, date=day, shift_code="N2"))
    session.commit()

    s_sub = summarize_month_for_person(2026, 3, 1, session=session)
    s_hourly = summarize_month_for_person(2026, 3, 2, session=session)
    d_sub = _summary_day(s_sub, day)
    d_hourly = _summary_day(s_hourly, day)

    assert sum(d_sub["ob_pay"].values()) > 0
    assert d_sub["ob_hours"] == d_hourly["ob_hours"]
    assert d_sub["ob_pay"] == d_hourly["ob_pay"]


def test_substitute_base_pay_added_per_hour(env):
    """The base pay for a substitute day is hours x hourly wage on top of the
    (untouched) monthly base; hours count into the month totals."""
    _, session = env
    day = datetime.date(2026, 3, 10)
    sub = _seed_linked_user(session, uid=1)
    session.add(SubstituteShift(substitute_id=sub.id, date=day, shift_code="N2"))
    session.commit()

    s = summarize_month_for_person(2026, 3, 1, session=session)
    sub_hours, _, _ = calculate_shift_hours(day, "N2")
    assert sub_hours > 0
    assert s["total_hours"] == pytest.approx(sub_hours)
    assert s["substitute_hours"] == pytest.approx(sub_hours)
    assert s["substitute_base_pay"] == pytest.approx(sub_hours * _SUB_WAGE)
    expected = s["base_salary"] + sub_hours * _SUB_WAGE + sum(s["ob_pay"].values())
    assert s["brutto_pay"] == pytest.approx(expected)


def test_substitute_ot_priced_with_hourly_rate_db_stays_zero(env):
    """Substitute overtime is priced with the hourly wage as the OT rate in the
    personal integration, while OvertimeShift.ot_pay stays 0.0 in the DB."""
    _, session = env
    day = datetime.date(2026, 3, 10)
    sub = _seed_linked_user(session, uid=1)
    session.add(
        OvertimeShift(
            substitute_id=sub.id,
            date=day,
            start_time=datetime.time(14, 0),
            end_time=datetime.time(22, 30),
            hours=8.5,
            ot_pay=0.0,
            is_extension=False,
        )
    )
    session.commit()

    s = summarize_month_for_person(2026, 3, 1, session=session)
    assert s["ot_pay"] == pytest.approx(8.5 * _SUB_WAGE)
    assert s["ot_hours"] == pytest.approx(8.5)
    d = _summary_day(s, day)
    assert d["ot_pay"] == pytest.approx(8.5 * _SUB_WAGE)

    row = session.query(OvertimeShift).filter(OvertimeShift.substitute_id == sub.id).first()
    assert row.ot_pay == 0.0


def test_mixed_month_brutto(env):
    """Transition month: substitute days (hourly) and employed days (monthly
    base) in the same month must both price correctly without touching the
    monthly base."""
    _, session = env
    emp_start = datetime.date(2026, 3, 16)
    sub = _seed_linked_user(session, uid=1, emp_start=emp_start)
    sub_dates = [datetime.date(2026, 3, 3), datetime.date(2026, 3, 4), datetime.date(2026, 3, 5)]
    for d in sub_dates:
        session.add(SubstituteShift(substitute_id=sub.id, date=d, shift_code="N2"))
    session.commit()

    s = summarize_month_for_person(2026, 3, 1, session=session)

    sub_hours = sum(calculate_shift_hours(d, "N2")[0] for d in sub_dates)
    assert s["substitute_hours"] == pytest.approx(sub_hours)
    # Employed days after emp_start contribute their own hours on top
    assert s["total_hours"] > sub_hours

    expected = (
        s["base_salary"]
        + sub_hours * _SUB_WAGE
        + sum(s["ob_pay"].values())
        + s["oncall_pay"]
        + s["ot_pay"]
        - s["absence_deduction"]
    )
    assert s["brutto_pay"] == pytest.approx(expected)

    # The substitute day's OB is priced with the hourly monthly-equivalent base
    d_sub = _summary_day(s, sub_dates[0])
    _, start, end = calculate_shift_hours(sub_dates[0], "N2")
    combined = get_combined_rules_for_year(2026)
    assert d_sub["ob_pay"] == calculate_ob_pay(start, end, combined, _MONTHLY_EQUIV)


def test_hourly_user_substitute_hours_not_double_priced(env):
    """For an HOURLY user, the hourly-corrected gross must exclude substitute
    hours (they are priced with the substitute wage, not the user's rate)."""
    _, session = env
    emp_start = datetime.date(2026, 3, 16)
    user_rate = 250
    sub = _seed_linked_user(session, uid=1, emp_start=emp_start, wage=user_rate, wage_type=WageType.HOURLY)
    day = datetime.date(2026, 3, 10)
    session.add(SubstituteShift(substitute_id=sub.id, date=day, shift_code="N2"))
    session.commit()

    s = summarize_month_for_person(2026, 3, 1, session=session)
    sub_hours, _, _ = calculate_shift_hours(day, "N2")
    worked_hours = s["total_hours"] - s["ot_hours"] - s["substitute_hours"]
    expected = (
        worked_hours * user_rate + sub_hours * _SUB_WAGE + sum(s["ob_pay"].values()) + s["oncall_pay"] + s["ot_pay"]
    )
    assert s["brutto_pay"] == pytest.approx(expected)


def test_month_grid_summary_includes_substitute_days(env):
    """The month view path (build_calendar_grid_for_month with an employment
    window) must not mask injected substitute days out of the summary totals."""
    _, session = env
    emp_start = datetime.date(2026, 6, 1)
    sub = _seed_linked_user(session, uid=1, emp_start=emp_start)
    day = datetime.date(2026, 3, 10)
    session.add(SubstituteShift(substitute_id=sub.id, date=day, shift_code="N2"))
    session.commit()

    data = build_calendar_grid_for_month(
        2026,
        3,
        1,
        session=session,
        employment_start=emp_start,
        viewer_user_id=1,
        wage_user_id=1,
    )
    ms = data["summary"]
    sub_hours, _, _ = calculate_shift_hours(day, "N2")
    assert ms["total_hours"] == pytest.approx(sub_hours)
    assert ms["substitute_base_pay"] == pytest.approx(sub_hours * _SUB_WAGE)
    assert sum(ms["ob_pay"].values()) > 0

    # The grid day itself renders the substitute shift
    grid_days = [d for w in data["grid"] for d in w if d and d.get("date") == day]
    assert grid_days and grid_days[0]["shift"].code == "N2"


# ---------------------------------------------------------------------------
# Report double-count guard (issue #290, plan step 5)
# ---------------------------------------------------------------------------


def test_report_attributes_linked_substitute_once(env):
    """A linked substitute's pre-employment worked day counts in the user's
    report row; the fully attributed substitute row is hidden."""
    _, session = env
    sub = _seed_linked_user(session, uid=1)
    day = datetime.date(2026, 3, 10)
    session.add(SubstituteShift(substitute_id=sub.id, date=day, shift_code="N2"))
    session.commit()

    rows = build_month_report(2026, 3, session)
    user_row = next(r for r in rows if r["person_id"] == 1 and not r["is_substitute"])
    hours, _, _ = calculate_shift_hours(day, "N2")
    assert user_row["total_hours"] == pytest.approx(round(hours, 1))
    assert user_row["num_shifts"] == 1

    sub_rows = [r for r in rows if r["is_substitute"] and r.get("substitute_id") == sub.id]
    assert sub_rows == [], "fully attributed substitute row must be hidden from the report"


def test_report_keeps_unlinked_substitute_row(env):
    """Unlinked substitutes keep their own report row, unchanged."""
    _, session = env
    _make_user(session, 1, 1)
    sub = Substitute(name="Extern vikarie", is_active=1)
    session.add(sub)
    session.commit()
    day = datetime.date(2026, 3, 10)
    session.add(SubstituteShift(substitute_id=sub.id, date=day, shift_code="N2"))
    session.commit()

    rows = build_month_report(2026, 3, session)
    sub_row = next(r for r in rows if r["is_substitute"] and r.get("substitute_id") == sub.id)
    hours, _, _ = calculate_shift_hours(day, "N2")
    assert sub_row["total_hours"] == pytest.approx(round(hours, 1))
    assert sub_row["num_shifts"] == 1


def test_report_partial_attribution_keeps_remaining_days(env):
    """Attributed worked days leave the substitute row, but activity the user
    row does not count (an absence day) stays on the substitute row."""
    _, session = env
    sub = _seed_linked_user(session, uid=1)
    worked = datetime.date(2026, 3, 10)
    sick = datetime.date(2026, 3, 12)
    session.add(SubstituteShift(substitute_id=sub.id, date=worked, shift_code="N2"))
    session.add(Absence(substitute_id=sub.id, date=sick, absence_type=AbsenceType.SICK))
    session.commit()

    rows = build_month_report(2026, 3, session)
    user_row = next(r for r in rows if r["person_id"] == 1 and not r["is_substitute"])
    hours, _, _ = calculate_shift_hours(worked, "N2")
    assert user_row["total_hours"] == pytest.approx(round(hours, 1))

    sub_row = next(r for r in rows if r["is_substitute"] and r.get("substitute_id") == sub.id)
    assert sub_row["sick_days"] == 1
    assert sub_row["total_hours"] == 0.0
    assert sub_row["num_shifts"] == 0


def test_report_ot_plus_shift_same_day_counts_once(env):
    """OT and a scheduled shift on the same attributed date count once (as OT)
    in the user's row, mirroring the canonical injection priority."""
    _, session = env
    sub = _seed_linked_user(session, uid=1)
    day = datetime.date(2026, 3, 10)
    session.add(SubstituteShift(substitute_id=sub.id, date=day, shift_code="N1"))
    session.add(
        OvertimeShift(
            substitute_id=sub.id,
            date=day,
            start_time=datetime.time(14, 0),
            end_time=datetime.time(22, 30),
            hours=8.5,
            ot_pay=0.0,
            is_extension=False,
        )
    )
    session.commit()

    rows = build_month_report(2026, 3, session)
    user_row = next(r for r in rows if r["person_id"] == 1 and not r["is_substitute"])
    assert user_row["ot_hours"] == pytest.approx(8.5)
    assert user_row["total_hours"] == pytest.approx(8.5)

    sub_rows = [r for r in rows if r["is_substitute"] and r.get("substitute_id") == sub.id]
    assert sub_rows == []
