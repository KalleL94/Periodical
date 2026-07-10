"""Integration tests for the /month team view under mid-month person changes.

The month matrix is publicly viewable (no login needed). Schedule internals read
the rotation era through the global SessionLocal, while routes read PersonHistory
through the get_db override. To make both see the same data, we bind a
monkeypatched SessionLocal to the same in-memory engine as test_db and seed a
RotationEra plus the PersonHistory rows there.
"""

import datetime
import re

import pytest
from sqlalchemy.orm import sessionmaker

import app.database.database as db_module
from app.auth.auth import create_access_token
from app.core.schedule import (
    clear_schedule_cache,
    generate_month_data,
    summarize_month_for_person,
    summarize_year_for_person,
)
from app.core.schedule.person_history import add_person_change, end_employment, start_employment
from app.core.utils import get_today
from app.database.database import RotationEra, User, UserRole, WageType
from tests.conftest import _ROTATION_ERA_PATTERN


def _make_user(session, uid, username, name, *, person_id=None, role=UserRole.USER):
    user = User(
        id=uid,
        username=username,
        password_hash="x",
        name=name,
        role=role,
        wage=30000,
        wage_type=WageType.MONTHLY,
        vacation={},
        must_change_password=0,
        is_active=0,
        person_id=person_id,
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture()
def month_env(test_db, test_client, monkeypatch):
    """Bind the global SessionLocal to test_db's engine and seed a rotation era.

    Yields (test_client, test_db) so schedule internals and the HTTP route share
    one in-memory database and both resolve the rotation and PersonHistory rows.
    """
    engine = test_db.get_bind()
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(db_module, "SessionLocal", SessionLocal)
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


def test_mid_month_change_shows_both_persons(month_env):
    client, session = month_env
    anna = _make_user(session, 11, "anna1", "Anna")
    bert = _make_user(session, 12, "bert1", "Bert")
    start_employment(session, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 2), created_by=1)
    add_person_change(
        session,
        old_user_id=anna.id,
        new_user_id=bert.id,
        person_id=3,
        new_name="Bert",
        new_username="bert1",
        effective_from=datetime.date(2026, 8, 15),
        created_by=1,
    )

    resp = client.get("/month?year=2026&month=8")

    assert resp.status_code == 200
    assert "Anna" in resp.text
    assert "Bert" in resp.text


def test_departed_person_absent_in_later_month(month_env):
    client, session = month_env
    anna = _make_user(session, 11, "anna1", "Anna")
    start_employment(session, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 2), created_by=1)
    end_employment(session, anna.id, 3, end_date=datetime.date(2026, 8, 4))

    resp = client.get("/month?year=2026&month=9")

    assert resp.status_code == 200
    assert "Anna" not in resp.text
    assert "Vakant" in resp.text or "Vacant" in resp.text


def test_mid_week_change_shows_both_rows(month_env):
    client, session = month_env
    anna = _make_user(session, 11, "anna1", "Anna")
    bert = _make_user(session, 12, "bert1", "Bert")
    # Thursday of ISO week 34, 2026: Anna holds the position until Wednesday,
    # Bert takes over from Thursday within the same week.
    thursday = datetime.date.fromisocalendar(2026, 34, 4)
    start_employment(session, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 2), created_by=1)
    add_person_change(
        session,
        old_user_id=anna.id,
        new_user_id=bert.id,
        person_id=3,
        new_name="Bert",
        new_username="bert1",
        effective_from=thursday,
        created_by=1,
    )

    resp = client.get("/week?year=2026&week=34")

    assert resp.status_code == 200
    assert "Anna" in resp.text
    assert "Bert" in resp.text


def test_departed_person_absent_in_later_week(month_env):
    client, session = month_env
    anna = _make_user(session, 11, "anna1", "Anna")
    start_employment(session, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 2), created_by=1)
    # End employment before ISO week 34, 2026: the position row is fully vacant.
    end_employment(session, anna.id, 3, end_date=datetime.date(2026, 8, 4))

    resp = client.get("/week?year=2026&week=34")

    assert resp.status_code == 200
    assert "Anna" not in resp.text
    assert "Vakant" in resp.text or "Vacant" in resp.text


def _year_header_ths(html: str, person_id: int) -> list[str]:
    """Return the full person-header <th> tags for a position's holder columns.

    The year view renders one column per holder segment, keyed by a col_key of
    the form "<person_id>-<user_id>" (or "<person_id>-vacant"), so a position
    can have several header cells. Each returned string is the whole <th ...>
    ... </th> tag including its attributes.
    """
    return re.findall(
        rf'(<th class="person-header[^"]*" data-person="{person_id}-[^"]*".*?</th>)',
        html,
        re.DOTALL,
    )


def test_year_header_vacant_after_departure(month_env):
    client, session = month_env
    anna = _make_user(session, 11, "anna1", "Anna")
    start_employment(session, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 2), created_by=1)
    # Anna held position 3 until 2026-08-04 with no successor. The 2027 view has
    # no holder overlapping that year, so the position-3 column must show the
    # vacancy label rather than the departed holder.
    end_employment(session, anna.id, 3, end_date=datetime.date(2026, 8, 4))

    resp = client.get("/year?year=2027")

    assert resp.status_code == 200
    ths = _year_header_ths(resp.text, 3)
    assert len(ths) == 1
    assert "Vakant" in ths[0] or "Vacant" in ths[0]
    assert "Anna" not in ths[0]


def test_year_splits_columns_per_holder_past_hidden(month_env):
    """A mid-year change yields one header column per holder, past one hidden.

    Isak held position 3 until a date in the past relative to today, Omar took
    over the day after. The year view for the current year must render two
    separate position-3 header cells (not a joined one), each linking to its
    holder's own personal year view. Isak's column carries the past marker and
    starts hidden; Omar's does not.
    """
    client, session = month_env
    admin = _make_user(session, 2, "admin1", "Admin", role=UserRole.ADMIN)
    isak = _make_user(session, 11, "isak1", "Isak")
    omar = _make_user(session, 12, "omar1", "Omar")

    today = get_today()
    isak_end = today - datetime.timedelta(days=10)
    omar_start = isak_end + datetime.timedelta(days=1)

    start_employment(session, isak.id, 3, "Isak", "isak1", datetime.date(2026, 1, 2), created_by=1)
    add_person_change(
        session,
        old_user_id=isak.id,
        new_user_id=omar.id,
        person_id=3,
        new_name="Omar",
        new_username="omar1",
        effective_from=omar_start,
        created_by=1,
    )

    # Authenticate as admin so the day drill-down links and totals row render.
    token = create_access_token(data={"sub": str(admin.id)})
    client.cookies.set("access_token", f"Bearer {token}")

    resp = client.get(f"/year?year={today.year}")

    assert resp.status_code == 200
    ths = _year_header_ths(resp.text, 3)
    assert len(ths) == 2

    isak_th = next(th for th in ths if "Isak" in th)
    omar_th = next(th for th in ths if "Omar" in th)
    # Separate cells, not a joined header.
    assert "Omar" not in isak_th
    assert "Isak" not in omar_th
    # Isak departed in the past: past marker set and column hidden by default.
    assert 'data-past="1"' in isak_th
    assert "display:none" in isak_th
    # Omar is current: no past marker, column visible.
    assert 'data-past="0"' in omar_th
    assert "display:none" not in omar_th
    # Each holder links to their own personal year view.
    assert f'/year/11?year={today.year}"' in isak_th
    assert f'/year/12?year={today.year}"' in omar_th


def _out_of_tenure_cells(html: str, col_key: str) -> list[str]:
    """Return the out-of-tenure day cells (whole <td>...</td>) for a column."""
    return re.findall(
        rf'(<td class="day-cell month-shift-cell out-of-tenure" data-person="{re.escape(col_key)}".*?</td>)',
        html,
        re.DOTALL,
    )


def test_year_out_of_tenure_cells_render_off(month_env):
    """Out-of-tenure day cells render a muted OFF badge instead of being empty."""
    client, session = month_env
    admin = _make_user(session, 2, "admin1", "Admin", role=UserRole.ADMIN)
    isak = _make_user(session, 11, "isak1", "Isak")
    omar = _make_user(session, 12, "omar1", "Omar")

    today = get_today()
    isak_end = today - datetime.timedelta(days=10)
    omar_start = isak_end + datetime.timedelta(days=1)

    start_employment(session, isak.id, 3, "Isak", "isak1", datetime.date(2026, 1, 2), created_by=1)
    add_person_change(
        session,
        old_user_id=isak.id,
        new_user_id=omar.id,
        person_id=3,
        new_name="Omar",
        new_username="omar1",
        effective_from=omar_start,
        created_by=1,
    )

    token = create_access_token(data={"sub": str(admin.id)})
    client.cookies.set("access_token", f"Bearer {token}")

    resp = client.get(f"/year?year={today.year}")
    assert resp.status_code == 200

    # Isak's column has out-of-tenure cells for days after his departure; they
    # must show OFF rather than be empty.
    isak_cells = _out_of_tenure_cells(resp.text, "3-11")
    assert isak_cells, "expected out-of-tenure cells for the departed holder"
    assert all("OFF" in cell for cell in isak_cells)


def test_year_summary_filters_to_viewed_users_employment(month_env):
    client, session = month_env
    admin = _make_user(session, 2, "admin1", "Admin", role=UserRole.ADMIN)
    anna = _make_user(session, 11, "anna1", "Anna")
    # Peter holds rotation position 3 and is the wage/employment user under test.
    peter = _make_user(session, 12, "peter1", "Peter", person_id=3)
    # Anna held position 3 from rotation start until Peter took over 2026-04-01
    # (add_person_change closes Anna 2026-03-31 and opens Peter 2026-04-01).
    start_employment(session, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 2), created_by=1)
    add_person_change(
        session,
        old_user_id=anna.id,
        new_user_id=peter.id,
        person_id=3,
        new_name="Peter",
        new_username="peter1",
        effective_from=datetime.date(2026, 4, 1),
        created_by=1,
    )

    data = summarize_year_for_person(
        2026,
        3,
        session=session,
        current_user=admin,
        wage_user_id=peter.id,
        employment_user_id=peter.id,
    )
    work_months = [(m["year"], m["month"]) for m in data["months"]]
    assert (2026, 4) in work_months
    # Nothing before Peter's employment start (2026-04) survives the filter,
    # even though the viewer is an admin.
    assert all(not (y < 2026 or (y == 2026 and mo < 4)) for y, mo in work_months)

    # HTTP level: /year/12 as an admin must not render the pre-employment work
    # months (Jan-Mar) while the first employed month (April) is present.
    token = create_access_token(data={"sub": str(admin.id)})
    client.cookies.set("access_token", f"Bearer {token}")
    resp = client.get("/year/12?year=2026")

    assert resp.status_code == 200
    # Closing quote anchors the match so month=1 does not collide with month=10.
    assert '/month/12?year=2026&month=4"' in resp.text
    assert '/month/12?year=2026&month=1"' not in resp.text
    assert '/month/12?year=2026&month=3"' not in resp.text


def test_year_by_user_id_shows_old_holder(month_env):
    """/year/<user_id> for an id <= 10 resolves to that USER, not the position.

    Robin (user id 10) held rotation position 10 until 2026-03-31; Peter (user
    id 12) took over from 2026-04-01. /year/10 must render Robin's data filtered
    to his employment (Jan-Mar), never the successor's later months.
    """
    client, session = month_env
    admin = _make_user(session, 2, "admin1", "Admin", role=UserRole.ADMIN)
    robin = _make_user(session, 10, "robin1", "Robin")
    peter = _make_user(session, 12, "peter1", "Peter", person_id=10)
    start_employment(session, robin.id, 10, "Robin", "robin1", datetime.date(2026, 1, 2), created_by=1)
    add_person_change(
        session,
        old_user_id=robin.id,
        new_user_id=peter.id,
        person_id=10,
        new_name="Peter",
        new_username="peter1",
        effective_from=datetime.date(2026, 4, 1),
        created_by=1,
    )

    token = create_access_token(data={"sub": str(admin.id)})
    client.cookies.set("access_token", f"Bearer {token}")
    resp = client.get("/year/10?year=2026")

    assert resp.status_code == 200
    # Robin (user 10) is the subject of the page, not Peter the successor.
    assert "Robin" in resp.text
    assert "Peter" not in resp.text
    # Robin's employment ended 2026-03-31: March present, April filtered out.
    assert '/month/10?year=2026&month=3"' in resp.text
    assert '/month/10?year=2026&month=4"' not in resp.text


def test_team_month_links_use_holder_user_ids(month_env):
    """Team month column headers link the holder's user id, not the position.

    A mid-month change at position 3 (Anna user 11 -> Bert user 12) yields two
    columns, each linking its holder's user id. The bare position link /month/3
    must not appear.
    """
    client, session = month_env
    anna = _make_user(session, 11, "anna1", "Anna")
    bert = _make_user(session, 12, "bert1", "Bert")
    start_employment(session, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 2), created_by=1)
    add_person_change(
        session,
        old_user_id=anna.id,
        new_user_id=bert.id,
        person_id=3,
        new_name="Bert",
        new_username="bert1",
        effective_from=datetime.date(2026, 8, 15),
        created_by=1,
    )

    resp = client.get("/month?year=2026&month=8")

    assert resp.status_code == 200
    assert "/month/11?year=2026&month=8" in resp.text
    assert "/month/12?year=2026&month=8" in resp.text
    # The changed position is never linked by its bare rotation position.
    assert "/month/3?year=2026&month=8" not in resp.text


def test_team_month_vacant_column_has_no_link(month_env):
    """A vacant position column renders no personal month link."""
    client, session = month_env
    admin = _make_user(session, 2, "admin1", "Admin", role=UserRole.ADMIN)
    anna = _make_user(session, 11, "anna1", "Anna")
    start_employment(session, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 2), created_by=1)
    end_employment(session, anna.id, 3, end_date=datetime.date(2026, 8, 4))

    # Authenticate as admin so day drill-down links render; otherwise the
    # /day/None assertion below is vacuous (day links only render when logged in).
    token = create_access_token(data={"sub": str(admin.id)})
    client.cookies.set("access_token", f"Bearer {token}")

    resp = client.get("/month?year=2026&month=9")

    assert resp.status_code == 200
    # Position 3 is vacant in September: neither the departed user nor the bare
    # position is linked for that column.
    assert "/month/11?year=2026&month=9" not in resp.text
    assert "/month/3?year=2026&month=9" not in resp.text
    # Vacant day cells must not emit a broken /day/None drill-down link.
    assert "/day/None" not in resp.text


def _august_total_ob(year_data: dict) -> float:
    """Return the August (work month 8) total_ob from a personal year summary."""
    for m in year_data["months"]:
        if m["year"] == 2026 and m["month"] == 8:
            return m["total_ob"]
    raise AssertionError("August work month missing from year summary")


def test_mid_month_change_splits_august_ob_between_holders(month_env):
    """A mid-August change must not credit the full month's OB to both holders.

    Anna holds position 3 until 2026-08-14, Bert from 2026-08-15. Each holder's
    personal year summary must count only their own days: the sum of the two
    August OB totals equals the unsplit position OB, and neither holder alone
    carries the whole month.
    """
    client, session = month_env
    admin = _make_user(session, 2, "admin1", "Admin", role=UserRole.ADMIN)
    anna = _make_user(session, 11, "anna1", "Anna", person_id=3)
    bert = _make_user(session, 12, "bert1", "Bert", person_id=3)
    start_employment(session, anna.id, 3, "Anna", "anna1", datetime.date(2026, 1, 2), created_by=admin.id)
    add_person_change(
        session,
        old_user_id=anna.id,
        new_user_id=bert.id,
        person_id=3,
        new_name="Bert",
        new_username="bert1",
        effective_from=datetime.date(2026, 8, 15),
        created_by=admin.id,
    )

    # Unsplit position OB for August, computed on unmasked days at the holders'
    # wage (both hold wage 30000, so OB pay is identical for either holder).
    unmasked_days = generate_month_data(2026, 8, 3, session=session)
    unsplit = summarize_month_for_person(
        2026, 8, 3, session=session, year_days=unmasked_days, wage_user_id=anna.id, payment_year=2026
    )
    unsplit_ob = sum(float(unsplit["ob_pay"].get(code, 0.0) or 0.0) for code in ("OB1", "OB2", "OB3", "OB4", "OB5"))
    assert unsplit_ob > 0  # the month must carry OB for the split to be meaningful

    anna_year = summarize_year_for_person(
        2026, 3, session=session, current_user=admin, wage_user_id=anna.id, employment_user_id=anna.id
    )
    bert_year = summarize_year_for_person(
        2026, 3, session=session, current_user=admin, wage_user_id=bert.id, employment_user_id=bert.id
    )
    anna_aug = _august_total_ob(anna_year)
    bert_aug = _august_total_ob(bert_year)

    # The two holders partition August OB: their sum reconstructs the full month,
    # and neither holder alone carries all of it.
    assert anna_aug + bert_aug == pytest.approx(unsplit_ob)
    assert anna_aug < unsplit_ob
    assert bert_aug < unsplit_ob


def test_year_totals_api_rejects_foreign_user_id(month_env):
    """Non-admin totals scoping is limited to legitimate holders of the position.

    A non-admin viewer at position 3 who passes ?user_id pointing at a user who
    never held position 3 gets 403 (otherwise they could back out that user's
    wage level). The request for the real holder still returns totals.
    """
    client, session = month_env
    admin = _make_user(session, 2, "admin1", "Admin", role=UserRole.ADMIN)
    # Peter is the non-admin viewer and the legitimate holder of position 3.
    peter = _make_user(session, 12, "peter1", "Peter", person_id=3)
    # Stranger exists but never held position 3.
    _make_user(session, 13, "stranger1", "Stranger", person_id=5)
    start_employment(session, peter.id, 3, "Peter", "peter1", datetime.date(2026, 1, 2), created_by=admin.id)

    token = create_access_token(data={"sub": str(peter.id)})
    client.cookies.set("access_token", f"Bearer {token}")

    # Foreign holder: forbidden.
    forbidden = client.get("/api/year/2026/totals/3?user_id=13")
    assert forbidden.status_code == 403

    # Legitimate holder (Peter himself): totals returned.
    allowed = client.get("/api/year/2026/totals/3?user_id=12")
    assert allowed.status_code == 200
    assert "total_ob" in allowed.json()
