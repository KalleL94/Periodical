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
from app.core.schedule import clear_schedule_cache, generate_period_data
from app.database.database import Absence, AbsenceType, RotationEra, User, UserRole, WageType
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
