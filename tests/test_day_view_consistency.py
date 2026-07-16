"""The personal day view must agree with the canonical period path
(generate_period_data) for the same person and date. The day route
re-implements shift resolution (issue #206) and has diverged before.
"""

import datetime
import re

import pytest
from sqlalchemy.orm import sessionmaker

import app.database.database as db_module
from app.auth.auth import create_access_token
from app.core.schedule import (
    calculate_ob_hours,
    calculate_ob_pay,
    calculate_shift_hours,
    clear_schedule_cache,
    determine_shift_for_date,
    generate_period_data,
    get_combined_rules_for_year,
)
from app.core.schedule.period import mask_days_to_employment
from app.core.schedule.person_history import get_employment_period
from app.database.database import (
    Absence,
    AbsenceType,
    DayPayOverride,
    OnCallOverride,
    OnCallOverrideType,
    OvertimeShift,
    PersonHistory,
    RotationEra,
    ShiftOverride,
    ShiftSwap,
    Substitute,
    SubstituteShift,
    SwapStatus,
    User,
    UserRole,
    WageType,
)
from tests.conftest import _ROTATION_ERA_PATTERN


@pytest.fixture()
def env(test_db, test_client, monkeypatch):
    """Bind the global SessionLocal to the test DB and seed the rotation era,
    so schedule internals and HTTP routes share one database (same technique as
    tests/test_schedule_views_person_change.py)."""
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


def _make_user(session, uid, person_id, vacation=None):
    user = User(
        id=uid,
        username=f"u{uid}",
        password_hash="x",
        name=f"User {uid}",
        role=UserRole.USER,
        wage=30000,
        wage_type=WageType.MONTHLY,
        vacation=vacation or {},
        must_change_password=0,
        is_active=1,
        person_id=person_id,
    )
    session.add(user)
    session.commit()
    return user


def _login(client, uid):
    client.cookies.set("access_token", create_access_token(data={"sub": str(uid)}))


def _oncall_totals(html: str) -> list[float]:
    """kr amounts in the first pay-section table footer (the on-call table when
    is_effective_oc is set)."""
    section = html.split("pay-section", 1)[1] if "pay-section" in html else html
    tfoot = re.search(r"<tfoot>.*?</tfoot>", section, re.S)
    if not tfoot:
        return []
    return [float(v) for v in re.findall(r"([\d.]+) kr", tfoot.group(0))]


def test_day_view_drops_oncall_on_full_day_sick_absence(env):
    client, session = env
    oc_date = datetime.date(2026, 3, 17)  # position 1 OC day in the seeded rotation
    _make_user(session, 1, 1)
    session.add(Absence(user_id=1, date=oc_date, absence_type=AbsenceType.SICK))
    session.commit()

    canonical = generate_period_data(oc_date, oc_date, person_id=1, session=session)[0]
    assert canonical["oncall_pay"] == 0.0
    assert canonical["shift"].code == "SICK"

    _login(client, 1)
    resp = client.get("/day/1/2026/3/17")
    assert resp.status_code == 200
    totals = _oncall_totals(resp.text)
    assert totals, "expected the on-call pay table to render for an OC day"
    assert max(totals) == 0.0, (
        f"day view pays {max(totals)} kr on-call on a fully sick OC day; the canonical period path pays 0"
    )


def test_day_view_masks_week_based_vacation_to_sem(env):
    client, session = env
    work_date = datetime.date(2026, 3, 2)  # position 1 N2 day, ISO week 10
    _make_user(session, 1, 1, vacation={"2026": [10]})

    canonical = generate_period_data(work_date, work_date, person_id=1, session=session)[0]
    assert canonical["shift"].code == "SEM"
    assert canonical["hours"] == 0.0
    assert canonical["ob"] == {}

    _login(client, 1)
    resp = client.get("/day/1/2026/3/2")
    assert resp.status_code == 200

    row = re.search(r"day-shift-row.*?</tr>", resp.text, re.S)
    assert row is not None
    hours_cells = re.findall(r"<td>([\d]+\.\d{2})</td>", row.group(0))
    assert "8.50" not in hours_cells, (
        f"day view shows worked hours {hours_cells} on a week-based vacation day; canonical path reports 0 hours (SEM)"
    )
    assert "0.00" in hours_cells


# ---------------------------------------------------------------------------
# Broadened consistency matrix (issue #206, plan step 0)
#
# Every override layer must render the same shift/hours/OB/on-call/OT in the day
# view as the canonical period path (generate_period_data). Each scenario seeds
# one layer, then asserts the rendered HTML against the canonical dict. This is
# the regression net that guards the migration of the day route onto the
# canonical path; it must stay green through every refactor step.
# ---------------------------------------------------------------------------

_BASE_SALARY = 30000


def _find_rotation_date(pid, predicate, start=datetime.date(2026, 1, 5), limit=120):
    """First date on/after ``start`` whose rotation shift code satisfies ``predicate``."""
    d = start
    for _ in range(limit):
        result = determine_shift_for_date(d, pid)
        code = result[0].code if result and result[0] else "OFF"
        if predicate(code):
            return d
        d += datetime.timedelta(days=1)
    raise AssertionError(f"no rotation date found for predicate near {start}")


def _canonical_day(session, pid, day):
    """Canonical day dict for the migrated day route: person-specific call with
    employment threading (before-start via employment_start, after-end via mask)."""
    emp_start, emp_end = get_employment_period(session, pid, pid)
    canonical = generate_period_data(day, day, person_id=pid, session=session, employment_start=emp_start)[0]
    if emp_end is not None and day > emp_end:
        canonical = mask_days_to_employment([canonical], datetime.date.min, emp_end)[0]
    return canonical


def _expected_ob(canonical, combined_rules):
    """Authoritative OB hours/pay for rendering, mirroring _process_day_for_summary
    (summary.py): honour ob_hours_override, otherwise compute from the shift only
    when it is a workable shift (not OFF/OC/OT) with concrete start/end times."""
    shift = canonical.get("shift")
    start = canonical.get("start")
    end = canonical.get("end")
    ob_hours_override = canonical.get("ob_hours_override")
    if ob_hours_override:
        from app.core.schedule.ob import apply_ob_hours_override

        return apply_ob_hours_override(ob_hours_override, _BASE_SALARY, combined_rules, None)
    if shift and shift.code not in ("OFF", "OC", "OT") and start and end:
        return (
            calculate_ob_hours(start, end, combined_rules),
            calculate_ob_pay(start, end, combined_rules, _BASE_SALARY, rate_overrides=None),
        )
    return ({r.code: 0.0 for r in combined_rules}, {r.code: 0.0 for r in combined_rules})


def _shift_row(html):
    m = re.search(r"day-shift-row.*?</tr>", html, re.S)
    assert m is not None, "day-shift-row not found in rendered day view"
    return m.group(0)


def _row_badges(row):
    return re.findall(r'class="badge[^"]*"[^>]*>\s*([A-Za-z0-9]+)\s*<', row)


def _row_hours(row):
    cells = re.findall(r"<td>([\d]+\.\d{2})</td>", row)
    return float(cells[-1]) if cells else None


def _pay_section(html):
    assert 'id="pay-section"' in html, "pay-section not rendered (salary hidden?)"
    return html.split('id="pay-section"', 1)[1]


def _oncall_total_kr(html):
    """On-call pay total from the pay-section. The only ' kr' suffixes inside the
    pay-section belong to the on-call table; its tfoot total is the last one."""
    vals = re.findall(r"([\d]+\.\d{2}) kr", _pay_section(html))
    return float(vals[-1]) if vals else None


def _ob_totals(html):
    """(total_ob_hours, total_ob_pay) from the two OB tfoots in the pay-section.
    Returns (None, None) when the on-call table rendered instead."""
    section = _pay_section(html)
    tfoots = re.findall(r"<tfoot>(.*?)</tfoot>", section, re.S)
    nums = [[float(x) for x in re.findall(r"[\d]+\.\d{2}", tf)] for tf in tfoots]
    if _oncall_total_kr(html) is not None:
        return None, None
    total_hours = nums[0][-1] if len(nums) >= 1 and nums[0] else 0.0
    total_pay = nums[1][-1] if len(nums) >= 2 and nums[1] else 0.0
    return total_hours, total_pay


def _ot_pay(html):
    m = re.search(r"kr/h</td>\s*<td>([\d.]+) kr", html)
    return float(m.group(1)) if m else None


def _assert_day_matches_canonical(client, session, uid, day):
    pid = uid  # test users use person_id == id
    canonical = _canonical_day(session, pid, day)
    resp = client.get(f"/day/{uid}/{day.year}/{day.month}/{day.day}")
    assert resp.status_code == 200
    html = resp.text
    combined_rules = get_combined_rules_for_year(day.year)

    # 1. Shift code + hours (shift resolution layer)
    row = _shift_row(html)
    expected_code = canonical["shift"].code if canonical.get("shift") else "OFF"
    badges = _row_badges(row)
    assert expected_code in badges, f"rendered badges {badges} lack canonical shift {expected_code}"
    assert _row_hours(row) == round(canonical.get("hours", 0.0) or 0.0, 2)

    # 2. On-call pay
    expected_oncall = round(canonical.get("oncall_pay", 0.0) or 0.0, 2)
    rendered_oncall = _oncall_total_kr(html)
    if expected_oncall > 0:
        assert rendered_oncall == expected_oncall, f"on-call {rendered_oncall} != canonical {expected_oncall}"
    else:
        assert rendered_oncall in (None, 0.0), f"day view shows on-call {rendered_oncall}; canonical pays 0"

    # 3. OB hours + pay (only when the OB section rendered, i.e. non-OC)
    exp_ob_hours, exp_ob_pay = _expected_ob(canonical, combined_rules)
    rendered_ob_hours, rendered_ob_pay = _ob_totals(html)
    if rendered_ob_hours is not None:
        assert rendered_ob_hours == round(sum(exp_ob_hours.values()), 2)
        assert rendered_ob_pay == round(sum(exp_ob_pay.values()), 2)

    # 4. Overtime pay
    expected_ot = round(canonical.get("ot_pay", 0.0) or 0.0, 2)
    if expected_ot > 0:
        assert _ot_pay(html) == expected_ot, f"OT pay {_ot_pay(html)} != canonical {expected_ot}"


# --- Scenario builders: each seeds one override layer and returns (uid, date) ---


def _sc_clean_work(session):
    _make_user(session, 1, 1)
    return 1, _find_rotation_date(1, lambda c: c in ("N1", "N2", "N3"))


def _sc_clean_oncall(session):
    _make_user(session, 1, 1)
    return 1, _find_rotation_date(1, lambda c: c == "OC")


def _sc_clean_off(session):
    _make_user(session, 1, 1)
    return 1, _find_rotation_date(1, lambda c: c == "OFF")


def _sc_shift_override(session):
    _make_user(session, 1, 1)
    day = _find_rotation_date(1, lambda c: c in ("N1", "N2"))
    session.add(ShiftOverride(user_id=1, date=day, shift_code="N3"))
    session.commit()
    return 1, day


def _sc_oncall_add(session):
    _make_user(session, 1, 1)
    day = _find_rotation_date(1, lambda c: c == "OFF")
    session.add(OnCallOverride(user_id=1, date=day, override_type=OnCallOverrideType.ADD))
    session.commit()
    return 1, day


def _sc_oncall_remove(session):
    _make_user(session, 1, 1)
    day = _find_rotation_date(1, lambda c: c == "OC")
    session.add(OnCallOverride(user_id=1, date=day, override_type=OnCallOverrideType.REMOVE))
    session.commit()
    return 1, day


def _sc_shift_swap(session):
    _make_user(session, 1, 1)
    _make_user(session, 2, 2)
    d1 = _find_rotation_date(1, lambda c: c in ("N1", "N2", "N3"))
    d2 = _find_rotation_date(2, lambda c: c in ("N1", "N2", "N3"), start=d1 + datetime.timedelta(days=1))
    session.add(
        ShiftSwap(
            requester_id=1,
            target_id=2,
            requester_date=d1,
            target_date=d2,
            status=SwapStatus.ACCEPTED,
        )
    )
    session.commit()
    return 1, d1


def _sc_week_vacation(session):
    day = datetime.date(2026, 3, 2)  # ISO week 10, position 1 N2 day
    _make_user(session, 1, 1, vacation={"2026": [10]})
    return 1, day


def _sc_day_vacation(session):
    _make_user(session, 1, 1)
    day = _find_rotation_date(1, lambda c: c in ("N1", "N2", "N3"))
    session.add(Absence(user_id=1, date=day, absence_type=AbsenceType.VACATION))
    session.commit()
    return 1, day


def _sc_partial_left(session):
    _make_user(session, 1, 1)
    # N1 is 06:00-14:30; leaving at 12:00 truncates within the shift.
    day = _find_rotation_date(1, lambda c: c == "N1")
    session.add(Absence(user_id=1, date=day, absence_type=AbsenceType.SICK, left_at="12:00"))
    session.commit()
    return 1, day


def _sc_partial_arrived(session):
    _make_user(session, 1, 1)
    # N1 is 06:00-14:30; arriving at 09:00 truncates within the shift.
    day = _find_rotation_date(1, lambda c: c == "N1")
    session.add(Absence(user_id=1, date=day, absence_type=AbsenceType.SICK, arrived_at="09:00"))
    session.commit()
    return 1, day


def _sc_full_sick(session):
    _make_user(session, 1, 1)
    day = _find_rotation_date(1, lambda c: c in ("N1", "N2", "N3"))
    session.add(Absence(user_id=1, date=day, absence_type=AbsenceType.SICK))
    session.commit()
    return 1, day


def _sc_week_parental(session):
    _make_user(session, 1, 1)
    user = session.query(User).filter(User.id == 1).first()
    user.parental_leave = {"2026": [10]}
    session.commit()
    return 1, datetime.date(2026, 3, 2)  # ISO week 10, scheduled day


def _sc_day_parental(session):
    _make_user(session, 1, 1)
    day = _find_rotation_date(1, lambda c: c in ("N1", "N2", "N3"))
    session.add(Absence(user_id=1, date=day, absence_type=AbsenceType.PARENTAL))
    session.commit()
    return 1, day


def _sc_day_pay_ob_override(session):
    _make_user(session, 1, 1)
    day = _find_rotation_date(1, lambda c: c in ("N1", "N2", "N3"))
    session.add(DayPayOverride(user_id=1, date=day, ob_hours_override={"OB1": 2.0, "OB3": 1.5}))
    session.commit()
    return 1, day


def _sc_day_pay_oncall_override(session):
    _make_user(session, 1, 1)
    day = _find_rotation_date(1, lambda c: c == "OC")
    session.add(DayPayOverride(user_id=1, date=day, oncall_hours_override={"OC_WEEKDAY": 24.0}))
    session.commit()
    return 1, day


def _sc_overtime_callin(session):
    _make_user(session, 1, 1)
    day = _find_rotation_date(1, lambda c: c == "OFF")
    session.add(
        OvertimeShift(
            user_id=1,
            date=day,
            start_time=datetime.time(14, 0),
            end_time=datetime.time(22, 30),
            hours=8.5,
            ot_pay=0.0,
            is_extension=False,
        )
    )
    session.commit()
    return 1, day


def _sc_overtime_extension(session):
    _make_user(session, 1, 1)
    day = _find_rotation_date(1, lambda c: c in ("N1", "N2", "N3"))
    session.add(
        OvertimeShift(
            user_id=1,
            date=day,
            start_time=datetime.time(22, 30),
            end_time=datetime.time(0, 30),
            hours=2.0,
            ot_pay=0.0,
            is_extension=True,
        )
    )
    session.commit()
    return 1, day


def _sc_before_employment(session):
    _make_user(session, 1, 1)
    # Employment starts in June; view a non-OC March day so no on-call table is
    # involved, isolating the before-employment OFF masking.
    session.add(
        PersonHistory(
            user_id=1,
            person_id=1,
            name="User 1",
            username="u1",
            is_active=1,
            effective_from=datetime.date(2026, 6, 1),
        )
    )
    session.commit()
    day = _find_rotation_date(1, lambda c: c in ("N1", "N2", "N3", "OFF"), start=datetime.date(2026, 3, 2), limit=20)
    return 1, day


# Every layer is a hard guard: since the day route resolves its shift through
# generate_period_data (issue #206), the six layers that used to diverge
# (shift swaps and parental leave were not handled at all, day-level vacation
# and full-day absences kept the underlying rotation shift, and an on-call ADD
# override derived 24h standby from the shift) now follow the canonical path
# and must stay identical to it.
_SCENARIOS = [
    _sc_clean_work,
    _sc_clean_oncall,
    _sc_clean_off,
    _sc_shift_override,
    _sc_oncall_add,
    _sc_oncall_remove,
    _sc_shift_swap,
    _sc_week_vacation,
    _sc_day_vacation,
    _sc_partial_left,
    _sc_partial_arrived,
    _sc_full_sick,
    _sc_week_parental,
    _sc_day_parental,
    _sc_day_pay_ob_override,
    _sc_day_pay_oncall_override,
    _sc_overtime_callin,
    _sc_overtime_extension,
    _sc_before_employment,
]


@pytest.mark.parametrize("scenario", _SCENARIOS, ids=[s.__name__[4:] for s in _SCENARIOS])
def test_day_view_matches_canonical(env, scenario):
    client, session = env
    uid, day = scenario(session)
    _login(client, uid)
    _assert_day_matches_canonical(client, session, uid, day)


# ---------------------------------------------------------------------------
# Linked substitute, pre-employment (issue #290)
#
# A substitute linked to a user account must have their pre-employment shifts
# injected by the canonical path (before-employment branch of
# _populate_single_person_day) and rendered by the personal day view, with the
# same priority chain as the team view (absence > OT > scheduled shift) and
# with rotation strictly winning on/after employment_start.
# ---------------------------------------------------------------------------

_SUB_HOURLY_WAGE = 200
_SUB_EMP_START = datetime.date(2026, 6, 1)


def _make_linked_substitute(session, uid=1, hourly_wage=_SUB_HOURLY_WAGE):
    """User employed from _SUB_EMP_START on position uid, plus a linked substitute."""
    _make_user(session, uid, uid)
    session.add(
        PersonHistory(
            user_id=uid,
            person_id=uid,
            name=f"User {uid}",
            username=f"u{uid}",
            is_active=1,
            effective_from=_SUB_EMP_START,
        )
    )
    sub = Substitute(name="Sommarvikarie", is_active=1, user_id=uid, hourly_wage=hourly_wage)
    session.add(sub)
    session.commit()
    return sub


def _canonical_sub_day(session, day, pid=1):
    return generate_period_data(day, day, person_id=pid, session=session, employment_start=_SUB_EMP_START)[0]


@pytest.mark.parametrize("code", ["N1", "N2", "N3", "OC"])
def test_canonical_injects_pre_employment_substitute_shift(env, code):
    _, session = env
    sub = _make_linked_substitute(session)
    day = datetime.date(2026, 3, 10)
    session.add(SubstituteShift(substitute_id=sub.id, date=day, shift_code=code))
    session.commit()

    canonical = _canonical_sub_day(session, day)
    assert canonical["shift"] is not None and canonical["shift"].code == code
    exp_hours, exp_start, exp_end = calculate_shift_hours(day, code)
    assert canonical["hours"] == exp_hours
    assert canonical["start"] == exp_start
    assert canonical["end"] == exp_end
    assert canonical.get("is_substitute") is True
    assert canonical.get("substitute_id") == sub.id
    assert canonical.get("substitute_hourly_wage") == _SUB_HOURLY_WAGE
    assert not canonical.get("before_employment")

    combined_rules = get_combined_rules_for_year(day.year)
    if code == "OC":
        assert canonical["ob"] == {}
    else:
        assert canonical["ob"] == calculate_ob_hours(exp_start, exp_end, combined_rules)


def test_canonical_substitute_absence_wins_over_shift(env):
    _, session = env
    sub = _make_linked_substitute(session)
    day = datetime.date(2026, 3, 10)
    session.add(SubstituteShift(substitute_id=sub.id, date=day, shift_code="N2"))
    session.add(Absence(substitute_id=sub.id, date=day, absence_type=AbsenceType.SICK))
    session.commit()

    canonical = _canonical_sub_day(session, day)
    assert canonical["shift"] is not None and canonical["shift"].code == "SICK"
    assert canonical["hours"] == 0.0
    assert canonical.get("is_substitute") is True


def test_canonical_substitute_ot_wins_over_shift(env):
    _, session = env
    sub = _make_linked_substitute(session)
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

    canonical = _canonical_sub_day(session, day)
    # OT takes display priority over the scheduled shift (no double counting)
    assert canonical["shift"] is not None and canonical["shift"].code == "OT"
    assert canonical["hours"] == 8.5
    assert canonical["ot_hours"] == 8.5
    assert canonical.get("is_substitute") is True


def test_substitute_shift_never_shown_on_or_after_employment_start(env):
    _, session = env
    sub = _make_linked_substitute(session)
    # A worked rotation day on/after employment_start with a (bogus) substitute
    # shift on the same date: the rotation must win outright.
    day = _find_rotation_date(1, lambda c: c in ("N1", "N2", "N3"), start=_SUB_EMP_START)
    rotation_code = determine_shift_for_date(day, 1)[0].code
    other_code = next(c for c in ("N1", "N2", "N3") if c != rotation_code)
    session.add(SubstituteShift(substitute_id=sub.id, date=day, shift_code=other_code))
    session.commit()

    canonical = _canonical_sub_day(session, day)
    assert canonical["shift"] is not None and canonical["shift"].code == rotation_code
    assert not canonical.get("is_substitute")


def test_unlinked_or_empty_days_keep_before_employment_off(env):
    _, session = env
    sub = _make_linked_substitute(session)
    # Substitute worked March 10; March 11 has no substitute data and must stay
    # a masked before-employment OFF day.
    session.add(SubstituteShift(substitute_id=sub.id, date=datetime.date(2026, 3, 10), shift_code="N1"))
    session.commit()

    canonical = _canonical_sub_day(session, datetime.date(2026, 3, 11))
    assert canonical["shift"] is not None and canonical["shift"].code == "OFF"
    assert canonical.get("before_employment") is True
    assert canonical["hours"] == 0.0


def test_week_path_renders_pre_employment_substitute_shift(env):
    """The week/range views resolve days via build_week_data, whose
    before-employment branch must inject the same substitute day."""
    from app.core.schedule.period import build_week_data

    _, session = env
    sub = _make_linked_substitute(session)
    day = datetime.date(2026, 3, 10)
    session.add(SubstituteShift(substitute_id=sub.id, date=day, shift_code="N2"))
    session.commit()

    iso_year, iso_week, _ = day.isocalendar()
    days = build_week_data(iso_year, iso_week, person_id=1, session=session, employment_start=_SUB_EMP_START)
    week_day = next(d for d in days if d["date"] == day)
    exp_hours, _, _ = calculate_shift_hours(day, "N2")
    assert week_day["shift"] is not None and week_day["shift"].code == "N2"
    assert week_day["hours"] == exp_hours
    assert week_day.get("is_substitute") is True
    assert not week_day.get("before_employment")

    # Other days of the week stay masked as before-employment OFF
    other = next(d for d in days if d["date"] == day + datetime.timedelta(days=1))
    assert other["shift"] is not None and other["shift"].code == "OFF"
    assert other.get("before_employment") is True


def test_day_view_renders_pre_employment_substitute_shift(env):
    client, session = env
    sub = _make_linked_substitute(session)
    day = datetime.date(2026, 3, 10)
    session.add(SubstituteShift(substitute_id=sub.id, date=day, shift_code="N2"))
    session.commit()

    canonical = _canonical_sub_day(session, day)
    assert canonical.get("is_substitute") is True

    _login(client, 1)
    resp = client.get(f"/day/1/{day.year}/{day.month}/{day.day}")
    assert resp.status_code == 200
    row = _shift_row(resp.text)
    badges = _row_badges(row)
    assert "N2" in badges, f"day view badges {badges} lack the substitute shift N2"
    assert _row_hours(row) == round(canonical["hours"], 2)

    # OB hours from the canonical dict must match the rendered OB table
    combined_rules = get_combined_rules_for_year(day.year)
    rendered_ob_hours, _ = _ob_totals(resp.text)
    exp_ob_hours = calculate_ob_hours(canonical["start"], canonical["end"], combined_rules)
    assert rendered_ob_hours == round(sum(exp_ob_hours.values()), 2)


def test_day_view_prices_substitute_ob_with_hourly_base(env):
    """OB kronor for a substitute day must be priced with the substitute's
    hourly wage as a monthly equivalent (hourly_wage x 173.33), exactly like
    an HOURLY user, not with the linked user's own monthly wage."""
    client, session = env
    sub = _make_linked_substitute(session)
    day = datetime.date(2026, 3, 10)
    session.add(SubstituteShift(substitute_id=sub.id, date=day, shift_code="N2"))
    session.commit()

    canonical = _canonical_sub_day(session, day)
    combined_rules = get_combined_rules_for_year(day.year)
    hourly_base = int(_SUB_HOURLY_WAGE * 173.33)
    exp_ob_pay = calculate_ob_pay(canonical["start"], canonical["end"], combined_rules, hourly_base)
    assert sum(exp_ob_pay.values()) > 0

    _login(client, 1)
    resp = client.get(f"/day/1/{day.year}/{day.month}/{day.day}")
    assert resp.status_code == 200
    _, rendered_ob_pay = _ob_totals(resp.text)
    assert rendered_ob_pay == round(sum(exp_ob_pay.values()), 2)


@pytest.mark.xfail(reason="issue #285: OT overlay applied after vacation resolution", strict=False)
def test_canonical_ot_on_vacation_week_keeps_sem(env):
    """OT overlay currently overrides a week-based vacation (SEM) day in the
    canonical path, so an OT shift booked on a vacation week replaces SEM. The
    day view inherits this once unified on the canonical path. Tracked as issue
    #285; asserts the desired behaviour (SEM wins) and is expected to fail until
    that fix lands (behaviour changed by a separate PR, so not strict)."""
    client, session = env
    day = datetime.date(2026, 3, 2)  # ISO week 10, position 1 scheduled day
    _make_user(session, 1, 1, vacation={"2026": [10]})
    session.add(
        OvertimeShift(
            user_id=1,
            date=day,
            start_time=datetime.time(14, 0),
            end_time=datetime.time(22, 30),
            hours=8.5,
            ot_pay=0.0,
            is_extension=False,
        )
    )
    session.commit()

    canonical = generate_period_data(day, day, person_id=1, session=session)[0]
    # Desired: vacation has priority over an OT overlay, so the day stays SEM.
    assert canonical["shift"].code == "SEM"
