"""End-to-end tests for the substitute (vikarie) feature."""

from datetime import date

from app.auth.auth import create_access_token
from app.database.database import Substitute, SubstituteShift


def test_substitute_full_flow(test_client, test_db, admin_user):
    # HTML admin routes authenticate via the access_token cookie
    token = create_access_token(data={"sub": str(admin_user.id)})
    test_client.cookies.set("access_token", token)
    admin_headers = {}

    # List page renders
    r = test_client.get("/admin/substitutes", headers=admin_headers)
    assert r.status_code == 200

    # Create a substitute
    r = test_client.post("/admin/substitutes/create", data={"name": "Sommarvikarie"}, headers=admin_headers)
    assert r.status_code == 200  # redirected (followed) to manage page
    sub = test_db.query(Substitute).filter(Substitute.name == "Sommarvikarie").first()
    assert sub is not None

    # Manage (calendar) page renders
    r = test_client.get(f"/admin/substitutes/{sub.id}?year=2026&month=7", headers=admin_headers)
    assert r.status_code == 200
    assert "shift_2026-07-06" in r.text  # calendar select present

    # Save a month with an N1 day and an OC day
    r = test_client.post(
        f"/admin/substitutes/{sub.id}/save",
        data={
            "year": "2026",
            "month": "7",
            "shift_2026-07-06": "N1",
            "shift_2026-07-07": "OC",
            "shift_2026-07-08": "",  # empty stays cleared
        },
        headers=admin_headers,
    )
    assert r.status_code == 200  # followed redirect back to calendar

    rows = test_db.query(SubstituteShift).filter(SubstituteShift.substitute_id == sub.id)
    shifts = {s.date: s.shift_code for s in rows}
    assert shifts == {date(2026, 7, 6): "N1", date(2026, 7, 7): "OC"}

    # Substitute appears in the month view
    r = test_client.get("/month?year=2026&month=7", headers=admin_headers)
    assert r.status_code == 200
    assert "Sommarvikarie" in r.text

    # ...and in the week view (ISO week of 2026-07-06)
    iso_year, iso_week, _ = date(2026, 7, 6).isocalendar()
    r = test_client.get(f"/week?year={iso_year}&week={iso_week}", headers=admin_headers)
    assert r.status_code == 200
    assert "Sommarvikarie" in r.text

    # ...and as someone working that day on a person's day view (coworker context)
    r = test_client.get("/day/1/2026/7/6", headers=admin_headers)
    assert r.status_code == 200
    assert "Sommarvikarie" in r.text

    # The single-person week and month views must not crash with a substitute present
    # (regression: substitute string id once broke the coworker sort)
    r = test_client.get(f"/week/1?year={iso_year}&week={iso_week}", headers=admin_headers)
    assert r.status_code == 200
    r = test_client.get("/month/1?year=2026&month=7", headers=admin_headers)
    assert r.status_code == 200

    # Clearing the N1 day removes it
    r = test_client.post(
        f"/admin/substitutes/{sub.id}/save",
        data={"year": "2026", "month": "7", "shift_2026-07-06": "", "shift_2026-07-07": "OC"},
        headers=admin_headers,
    )
    assert r.status_code == 200
    rows = test_db.query(SubstituteShift).filter(SubstituteShift.substitute_id == sub.id)
    shifts = {s.date: s.shift_code for s in rows}
    assert shifts == {date(2026, 7, 7): "OC"}


def test_substitute_shown_as_coworker_in_personal_month(rotation_session):
    """A substitute working the same shift appears as a coworker in the personal month calendar."""
    from app.core.schedule import clear_schedule_cache
    from app.core.schedule.summary import build_calendar_grid_for_month

    # Find a working day for rotation position 1 in this month
    grid = build_calendar_grid_for_month(2026, 7, person_id=1, session=rotation_session, include_coworkers=True)
    target = None
    code = None
    for week in grid["grid"]:
        for dd in week:
            if dd.get("is_current_month") and dd.get("shift") and dd["shift"].code in ("N1", "N2", "N3"):
                target, code = dd["date"], dd["shift"].code
                break
        if target:
            break
    assert target is not None, "expected at least one working day for position 1"

    sub = Substitute(name="VikKollega", is_active=1)
    rotation_session.add(sub)
    rotation_session.commit()
    rotation_session.add(SubstituteShift(substitute_id=sub.id, date=target, shift_code=code))
    rotation_session.commit()
    clear_schedule_cache()

    grid2 = build_calendar_grid_for_month(2026, 7, person_id=1, session=rotation_session, include_coworkers=True)
    found = False
    for week in grid2["grid"]:
        for dd in week:
            if dd["date"] == target:
                assert "VikKollega" in dd.get("coworkers", [])
                found = True
    assert found
