"""Characterization tests for the /api/v1 schedule endpoints.

The external Home Assistant integration consumes these endpoints, so the response
CONTRACT (key names, types, nesting, status codes) is frozen here before api_v1.py
is rebuilt on the canonical period path. Values may become more correct for users
with swaps/overrides, so the value assertions below deliberately use plain rotation
days that no override layer touches.

Same technique as tests/test_day_view_consistency.py: bind the global SessionLocal
to the test DB and seed a rotation era so schedule internals and HTTP routes share
one database.
"""

import datetime

import pytest
from sqlalchemy.orm import sessionmaker

import app.database.database as db_module
from app.auth.auth import hash_api_key
from app.core.schedule import clear_schedule_cache, determine_shift_for_date, generate_period_data
from app.database.database import (
    Absence,
    AbsenceType,
    OnCallOverride,
    OnCallOverrideType,
    OvertimeShift,
    RotationEra,
    ShiftOverride,
    ShiftSwap,
    SwapStatus,
    User,
    UserRole,
    WageType,
)
from tests.conftest import _ROTATION_ERA_PATTERN

API_KEY = "char-user-key"
ADMIN_KEY = "char-admin-key"
AUTH = {"Authorization": f"Bearer {API_KEY}"}
ADMIN_AUTH = {"Authorization": f"Bearer {ADMIN_KEY}"}

# Dates inside the seeded era (era starts 2026-01-02).
N2_DAY = datetime.date(2026, 3, 2)  # position 1 works N2
OFF_DAY = datetime.date(2026, 3, 7)  # position 1 is OFF
OC_DAY = datetime.date(2026, 3, 17)  # position 1 has on-call
SICK_DAY = datetime.date(2026, 3, 3)  # absence seeded below
PARTIAL_DAY = datetime.date(2026, 3, 4)  # partial absence seeded below
OT_DAY = datetime.date(2026, 3, 9)  # overtime seeded below

SHIFT_KEYS = {"code", "label", "start_time", "end_time", "color", "overnight"}
DAY_KEYS = {"date", "status", "shift", "rotation_week", "overtime", "partial_day"}
SALARY_KEYS = {"ob_pay", "ob_total"}


def _make_user(session, uid, person_id, name, role=UserRole.USER, api_key=None):
    user = User(
        id=uid,
        username=f"u{uid}",
        password_hash="x",
        name=name,
        role=role,
        wage=30000,
        wage_type=WageType.MONTHLY,
        vacation={},
        must_change_password=0,
        is_active=1,
        person_id=person_id,
        api_key=hash_api_key(api_key) if api_key else None,
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture()
def api_env(test_db, test_client, monkeypatch):
    engine = test_db.get_bind()
    monkeypatch.setattr(db_module, "SessionLocal", sessionmaker(autocommit=False, autoflush=False, bind=engine))
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
    _make_user(test_db, 1, 1, "User One", api_key=API_KEY)
    _make_user(test_db, 2, 2, "User Two")
    _make_user(test_db, 3, 3, "User Three")
    _make_user(test_db, 4, 4, "Admin", role=UserRole.ADMIN, api_key=ADMIN_KEY)
    test_db.add(Absence(user_id=1, date=SICK_DAY, absence_type=AbsenceType.SICK))
    test_db.add(Absence(user_id=1, date=PARTIAL_DAY, absence_type=AbsenceType.SICK, left_at="18:00"))
    test_db.add(
        OvertimeShift(
            user_id=1,
            date=OT_DAY,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(16, 0),
            hours=8.0,
            ot_pay=0.0,
            is_extension=False,
        )
    )
    test_db.commit()
    yield test_client, test_db
    clear_schedule_cache()


def _get(client, path, headers=AUTH):
    resp = client.get(path, headers=headers)
    assert resp.status_code == 200, f"{path} -> {resp.status_code} {resp.text}"
    return resp.json()


def _assert_shift_shape(shift):
    assert set(shift) == SHIFT_KEYS
    assert isinstance(shift["code"], str)
    assert isinstance(shift["label"], str)
    assert isinstance(shift["overnight"], bool)
    for key in ("start_time", "end_time", "color"):
        assert shift[key] is None or isinstance(shift[key], str)


def _assert_day_shape(day, salary=False, coworkers=False, absence=False):
    expected = set(DAY_KEYS)
    if absence:
        expected |= {"arrived_late"}
    if salary:
        expected |= SALARY_KEYS
    if coworkers:
        expected |= {"coworkers"}
    assert set(day) == expected, f"unexpected keys: {sorted(set(day) ^ expected)}"
    assert isinstance(day["date"], str)
    assert isinstance(day["status"], str)
    assert day["shift"] is None or _assert_shift_shape(day["shift"]) is None
    assert day["rotation_week"] is None or isinstance(day["rotation_week"], int)
    if day["overtime"] is not None:
        assert set(day["overtime"]) == {"start_time", "end_time", "hours", "is_extension"}
        assert isinstance(day["overtime"]["hours"], int | float)
        assert isinstance(day["overtime"]["is_extension"], bool)
    assert day["partial_day"] is None or isinstance(day["partial_day"], str)
    if salary:
        assert day["ob_pay"] is None or isinstance(day["ob_pay"], dict)
        assert isinstance(day["ob_total"], int | float)
    if coworkers:
        assert isinstance(day["coworkers"], list)
        for cw in day["coworkers"]:
            assert set(cw) == {"id", "name", "shift_code", "shift_label"}
            assert isinstance(cw["id"], int)
            assert isinstance(cw["name"], str)
            assert isinstance(cw["shift_code"], str)
            assert isinstance(cw["shift_label"], str)


class TestSimpleEndpoints:
    def test_me(self, api_env):
        client, _ = api_env
        body = _get(client, "/api/v1/me")
        assert set(body) == {"id", "name", "username", "role", "is_active", "rotation_person_id"}
        assert body == {
            "id": 1,
            "name": "User One",
            "username": "u1",
            "role": "user",
            "is_active": True,
            "rotation_person_id": 1,
        }

    def test_shifts(self, api_env):
        client, _ = api_env
        body = _get(client, "/api/v1/shifts")
        assert isinstance(body, list) and body
        for shift in body:
            _assert_shift_shape(shift)
        codes = {s["code"] for s in body}
        assert {"N1", "N2", "N3", "OFF", "OC", "OT-N1", "OT-N2", "OT-N3"} <= codes

    def test_users(self, api_env):
        client, _ = api_env
        body = _get(client, "/api/v1/users")
        assert body == [
            {"id": 4, "name": "Admin"},
            {"id": 1, "name": "User One"},
            {"id": 3, "name": "User Three"},
            {"id": 2, "name": "User Two"},
        ]

    def test_absences(self, api_env):
        client, _ = api_env
        body = _get(client, "/api/v1/users/1/absences?year=2026")
        assert [set(a) for a in body] == [{"id", "date", "type", "partial_day", "arrived_late"}] * 2
        assert body[0]["date"] == SICK_DAY.isoformat()
        assert body[0]["type"] == "SICK"
        assert body[1]["partial_day"] == "18:00"


class TestDayShape:
    def test_status_plain_working_day(self, api_env):
        client, _ = api_env
        body = _get(client, f"/api/v1/users/1/status?date={N2_DAY}&time=06:00")
        _assert_day_shape(body, salary=True, coworkers=True)
        assert body["date"] == N2_DAY.isoformat()
        assert body["status"] == "working"
        assert body["shift"]["code"] == "N2"
        assert body["overtime"] is None
        assert body["partial_day"] is None
        assert body["ob_total"] > 0

    def test_status_off_day(self, api_env):
        client, _ = api_env
        body = _get(client, f"/api/v1/users/1/status?date={OFF_DAY}&time=06:00")
        _assert_day_shape(body, salary=True, coworkers=True)
        assert body["status"] == "off"
        assert body["shift"]["code"] == "OFF"
        assert body["ob_pay"] is None
        assert body["ob_total"] == 0.0

    def test_status_oncall_day(self, api_env):
        client, _ = api_env
        body = _get(client, f"/api/v1/users/1/status?date={OC_DAY}&time=06:00")
        _assert_day_shape(body, salary=True, coworkers=True)
        assert body["status"] == "working"
        assert body["shift"]["code"] == "OC"
        assert body["ob_pay"] is None

    def test_status_absence_day_has_arrived_late_key(self, api_env):
        client, _ = api_env
        body = _get(client, f"/api/v1/users/1/status?date={SICK_DAY}&time=06:00")
        _assert_day_shape(body, salary=True, coworkers=True, absence=True)
        assert body["status"] == "sick"
        assert body["partial_day"] is None
        assert body["arrived_late"] is None

    def test_status_partial_absence_reports_left_at(self, api_env):
        client, _ = api_env
        body = _get(client, f"/api/v1/users/1/status?date={PARTIAL_DAY}&time=06:00")
        _assert_day_shape(body, salary=True, coworkers=True, absence=True)
        assert body["status"] == "sick"
        assert body["partial_day"] == "18:00"

    def test_status_overtime_day_carries_overtime_block(self, api_env):
        client, _ = api_env
        body = _get(client, f"/api/v1/users/1/status?date={OT_DAY}&time=06:00")
        _assert_day_shape(body, salary=True, coworkers=True)
        assert body["overtime"] == {
            "start_time": "08:00",
            "end_time": "16:00",
            "hours": 8.0,
            "is_extension": False,
        }

    def test_schedule_today_shape(self, api_env):
        client, _ = api_env
        body = _get(client, f"/api/v1/users/1/schedule/today?date={N2_DAY}")
        _assert_day_shape(body, salary=True, coworkers=True)

    def test_schedule_specific_date_shape(self, api_env):
        client, _ = api_env
        body = _get(client, f"/api/v1/users/1/schedule/{N2_DAY}")
        _assert_day_shape(body, salary=True, coworkers=True)
        assert body["shift"]["code"] == "N2"

    def test_other_user_day_omits_salary(self, api_env):
        client, _ = api_env
        body = _get(client, f"/api/v1/users/2/schedule/{N2_DAY}")
        _assert_day_shape(body, salary=False, coworkers=True)

    def test_admin_key_sees_salary_for_other_user(self, api_env):
        client, _ = api_env
        body = _get(client, f"/api/v1/users/1/schedule/{N2_DAY}", headers=ADMIN_AUTH)
        _assert_day_shape(body, salary=True, coworkers=True)


class TestPeriodEndpoints:
    def test_week(self, api_env):
        client, _ = api_env
        body = _get(client, f"/api/v1/users/1/schedule/week/{N2_DAY}")
        assert set(body) == {"week", "year", "days"}
        assert body["week"] == N2_DAY.isocalendar()[1]
        assert body["year"] == 2026
        assert len(body["days"]) == 7
        for day in body["days"]:
            _assert_day_shape(day, salary=True, coworkers=True, absence="arrived_late" in day)
        assert [d["date"] for d in body["days"]][0] == "2026-03-02"

    def test_range(self, api_env):
        client, _ = api_env
        body = _get(client, "/api/v1/users/1/schedule?from_date=2026-03-02&to_date=2026-03-08")
        assert set(body) == {"days"}
        assert len(body["days"]) == 7
        for day in body["days"]:
            _assert_day_shape(day, salary=True, coworkers=True, absence="arrived_late" in day)

    def test_month(self, api_env):
        client, _ = api_env
        body = _get(client, "/api/v1/users/1/schedule/month")
        assert set(body) == {"month", "year", "days", "wage", "ob_total"}
        today = datetime.date.today()
        assert body["month"] == today.month
        assert body["year"] == today.year
        assert body["wage"] == 30000
        assert isinstance(body["ob_total"], int | float)
        for day in body["days"]:
            _assert_day_shape(day, salary=True, coworkers=True, absence="arrived_late" in day)

    def test_year(self, api_env):
        client, _ = api_env
        body = _get(client, "/api/v1/users/1/schedule/year?year=2026")
        assert set(body) == {"year", "days", "wage", "ob_total"}
        assert body["year"] == 2026
        assert len(body["days"]) == 365
        assert body["days"][0]["date"] == "2026-01-01"
        assert body["days"][-1]["date"] == "2026-12-31"
        for day in body["days"]:
            _assert_day_shape(day, salary=True, coworkers=True, absence="arrived_late" in day)

    def test_year_ob_total_is_sum_of_days(self, api_env):
        client, _ = api_env
        body = _get(client, "/api/v1/users/1/schedule/year?year=2026")
        assert body["ob_total"] == pytest.approx(round(sum(d["ob_total"] for d in body["days"]), 2), abs=0.01)


class TestNextShift:
    def test_next_shift_shape(self, api_env):
        client, _ = api_env
        body = _get(client, f"/api/v1/users/1/next-shift?date={OFF_DAY}&time=06:00")
        assert set(body) == {"date", "days_from_today", "shift", "rotation_week"}
        _assert_shift_shape(body["shift"])
        assert isinstance(body["days_from_today"], int)
        assert body["shift"]["code"] != "OFF"

    def test_next_shift_404_outside_horizon(self, api_env):
        client, _ = api_env
        resp = client.get("/api/v1/users/1/next-shift?date=2025-01-01&time=06:00", headers=AUTH)
        assert resp.status_code == 404


class TestErrorContract:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("/api/v1/users/999/status", 404),
            ("/api/v1/users/1/status?date=not-a-date", 400),
            ("/api/v1/users/1/status?date=2026-03-02&time=99", 400),
            ("/api/v1/users/1/schedule/today?date=nope", 400),
            ("/api/v1/users/1/schedule/week/nope", 400),
            ("/api/v1/users/1/schedule/nope", 400),
            ("/api/v1/users/1/schedule?from_date=2026-03-02&to_date=2026-03-01", 400),
            ("/api/v1/users/1/schedule?from_date=2026-01-01&to_date=2026-12-31", 400),
            ("/api/v1/users/2/pay/month", 403),
            ("/api/v1/users/2/vacation/balance", 403),
            ("/api/v1/users/2/absences", 403),
        ],
    )
    def test_status_codes(self, api_env, path, expected):
        client, _ = api_env
        assert client.get(path, headers=AUTH).status_code == expected


class TestCanonicalConsistency:
    """The API must resolve a day the same way the canonical period path does.

    api_v1 used to resolve days with determine_shift_for_date directly, so swaps,
    shift overrides and week-based vacation were invisible to Home Assistant while
    the browser showed them (issue: shadow schedule engine in api_v1.py).
    """

    def test_accepted_swap_is_reflected_in_day_endpoints(self, api_env):
        client, session = api_env
        swap_day = N2_DAY
        other_day = datetime.date(2026, 3, 23)
        session.add(
            ShiftSwap(
                requester_id=1,
                target_id=2,
                requester_date=swap_day,
                target_date=other_day,
                requester_shift_code="N2",
                target_shift_code=determine_shift_for_date(other_day, 2)[0].code,
                status=SwapStatus.ACCEPTED,
            )
        )
        session.commit()
        clear_schedule_cache()

        canonical = generate_period_data(swap_day, swap_day, person_id=1, session=session)[0]
        rotation_code = determine_shift_for_date(swap_day, 1)[0].code
        assert canonical["shift"].code != rotation_code, "fixture must produce a visible swap"

        for path in (
            f"/api/v1/users/1/status?date={swap_day}&time=06:00",
            f"/api/v1/users/1/schedule/today?date={swap_day}",
            f"/api/v1/users/1/schedule/{swap_day}",
        ):
            body = _get(client, path)
            assert body["shift"]["code"] == canonical["shift"].code, path

        week = _get(client, f"/api/v1/users/1/schedule/week/{swap_day}")
        day = next(d for d in week["days"] if d["date"] == swap_day.isoformat())
        assert day["shift"]["code"] == canonical["shift"].code

        year = _get(client, "/api/v1/users/1/schedule/year?year=2026")
        day = next(d for d in year["days"] if d["date"] == swap_day.isoformat())
        assert day["shift"]["code"] == canonical["shift"].code

    def test_shift_override_is_reflected(self, api_env):
        client, session = api_env
        override_day = OFF_DAY
        session.add(ShiftOverride(user_id=1, date=override_day, shift_code="N1"))
        session.commit()
        clear_schedule_cache()

        body = _get(client, f"/api/v1/users/1/schedule/{override_day}")
        assert body["shift"]["code"] == "N1"
        assert body["status"] == "working"

    def test_week_based_vacation_is_reflected(self, api_env):
        client, session = api_env
        user = session.query(User).filter(User.id == 1).first()
        user.vacation = {"2026": [10]}  # ISO week 10 contains N2_DAY
        session.commit()
        clear_schedule_cache()

        canonical = generate_period_data(N2_DAY, N2_DAY, person_id=1, session=session)[0]
        assert canonical["shift"].code == "SEM"

        # The API reports absence and vacation through `status`, keeping `shift` as the
        # shift the person was assigned (same split as a day-level VACATION absence).
        body = _get(client, f"/api/v1/users/1/schedule/{N2_DAY}")
        assert body["status"] == "vacation"
        assert body["shift"]["code"] == "N2"
        assert body["ob_pay"] is None
        assert body["ob_total"] == 0.0

    def test_oncall_override_is_reflected(self, api_env):
        client, session = api_env
        session.add(OnCallOverride(user_id=1, date=OFF_DAY, override_type=OnCallOverrideType.ADD))
        session.commit()
        clear_schedule_cache()

        body = _get(client, f"/api/v1/users/1/schedule/{OFF_DAY}")
        assert body["shift"]["code"] == "OC"


class TestAdminEndpoints:
    def test_team_today(self, api_env):
        client, _ = api_env
        body = _get(client, "/api/v1/admin/team/today", headers=ADMIN_AUTH)
        assert set(body) == {"date", "team"}
        assert isinstance(body["date"], str)
        for entry in body["team"]:
            assert {"id", "name"} <= set(entry)
            day = {k: v for k, v in entry.items() if k not in ("id", "name")}
            _assert_day_shape(day, absence="arrived_late" in day)

    def test_team_schedule_range(self, api_env):
        client, _ = api_env
        body = _get(
            client,
            "/api/v1/admin/team/schedule?from_date=2026-03-02&to_date=2026-03-08",
            headers=ADMIN_AUTH,
        )
        assert set(body) == {"from_date", "to_date", "team"}
        for entry in body["team"]:
            assert set(entry) == {"id", "name", "days"}
            assert len(entry["days"]) == 7
            for day in entry["days"]:
                _assert_day_shape(day, coworkers=True, absence="arrived_late" in day)

    def test_team_schedule_rejects_long_range(self, api_env):
        client, _ = api_env
        resp = client.get(
            "/api/v1/admin/team/schedule?from_date=2026-03-02&to_date=2026-04-02",
            headers=ADMIN_AUTH,
        )
        assert resp.status_code == 400
