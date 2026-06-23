"""Tests for the monthly report and overtime/absence on substitutes."""

from app.auth.auth import create_access_token
from app.core.schedule import build_month_report
from app.database.database import Absence, OvertimeShift, Substitute


def _login_admin(test_client, admin_user):
    token = create_access_token(data={"sub": str(admin_user.id)})
    test_client.cookies.set("access_token", token)


def test_substitute_ot_and_absence_flow(test_client, test_db, admin_user):
    _login_admin(test_client, admin_user)

    # Create a substitute
    test_client.post("/admin/substitutes/create", data={"name": "Vik Sommar"})
    sub = test_db.query(Substitute).filter(Substitute.name == "Vik Sommar").first()
    assert sub is not None

    # Add an overtime entry (hours only, no pay)
    r = test_client.post(
        f"/admin/substitutes/{sub.id}/overtime/add",
        data={"date": "2026-07-10", "start_time": "08:00", "end_time": "12:30", "hours": "4.5"},
    )
    assert r.status_code == 200  # followed redirect to manage page

    ot = test_db.query(OvertimeShift).filter(OvertimeShift.substitute_id == sub.id).one()
    assert ot.user_id is None
    assert ot.hours == 4.5
    assert ot.ot_pay == 0.0

    # Add an absence day
    r = test_client.post(
        f"/admin/substitutes/{sub.id}/absence/add",
        data={"date": "2026-07-11", "absence_type": "SICK"},
    )
    assert r.status_code == 200
    absence = test_db.query(Absence).filter(Absence.substitute_id == sub.id).one()
    assert absence.user_id is None
    assert str(absence.absence_type) == "SICK"

    # Add a vacation day (substitutes can have vacation registered manually too)
    r = test_client.post(
        f"/admin/substitutes/{sub.id}/absence/add",
        data={"date": "2026-07-14", "absence_type": "VACATION"},
    )
    assert r.status_code == 200

    # The manage page shows both entries
    r = test_client.get(f"/admin/substitutes/{sub.id}?year=2026&month=7")
    assert r.status_code == 200
    assert "4.5" in r.text
    assert "SICK" in r.text

    # Report reflects the substitute's hours, sick day and vacation day
    rows = build_month_report(2026, 7, test_db, fetch_tax_table=False)
    sub_row = next(row for row in rows if row["substitute_id"] == sub.id)
    assert sub_row["is_substitute"] is True
    assert sub_row["ot_hours"] == 4.5
    assert sub_row["total_hours"] == 4.5
    assert sub_row["sick_days"] == 1
    assert sub_row["vacation_days"] == 1
    assert "brutto_pay" not in sub_row  # report tracks time only, no salary

    # Invalid absence type is rejected
    r = test_client.post(
        f"/admin/substitutes/{sub.id}/absence/add",
        data={"date": "2026-07-12", "absence_type": "BOGUS"},
    )
    assert r.status_code == 400

    # Deleting the overtime entry removes it
    r = test_client.post(f"/admin/substitutes/{sub.id}/overtime/{ot.id}/delete")
    assert r.status_code == 200
    assert test_db.query(OvertimeShift).filter(OvertimeShift.substitute_id == sub.id).count() == 0


def test_report_page_and_csv(test_client, test_db, admin_user):
    _login_admin(test_client, admin_user)

    # Add a substitute with a worked day so it appears in the report
    test_client.post("/admin/substitutes/create", data={"name": "Rapportvik"})
    sub = test_db.query(Substitute).filter(Substitute.name == "Rapportvik").first()
    test_client.post(
        f"/admin/substitutes/{sub.id}/save",
        data={"year": "2026", "month": "7", "shift_2026-07-06": "N1"},
    )

    # HTML report renders with the substitute row
    r = test_client.get("/admin/report?year=2026&month=7")
    assert r.status_code == 200
    assert "Månadsrapport" in r.text
    assert "Rapportvik" in r.text

    # The substitute's worked N1 shift contributes night OB (OB2) to the report
    rows = build_month_report(2026, 7, test_db)
    sub_row = next(row for row in rows if row["substitute_id"] == sub.id)
    assert sub_row["ob_natt"] == 1.0
    assert sub_row["ob_kvall"] == 0.0
    assert sub_row["ob_helg"] == 0.0
    assert sub_row["ob_storhelg"] == 0.0

    # CSV export downloads with the right headers and content
    r = test_client.get("/admin/report.csv?year=2026&month=7")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert "rapport-2026-07.csv" in r.headers["content-disposition"]
    body = r.content.decode("utf-8-sig")
    assert "Namn;Antal pass" in body
    assert "Rapportvik" in body


def test_substitute_overtime_on_oncall_day(test_client, test_db, admin_user):
    """Overtime on an on-call day shows as OT in the month view and reduces standby hours."""
    from datetime import date

    from app.core.schedule.period import build_substitute_month_summaries

    _login_admin(test_client, admin_user)

    test_client.post("/admin/substitutes/create", data={"name": "Beredskapsvik"})
    sub = test_db.query(Substitute).filter(Substitute.name == "Beredskapsvik").first()

    # On-call (OC) day = 24h standby
    test_client.post(
        f"/admin/substitutes/{sub.id}/save",
        data={"year": "2026", "month": "7", "shift_2026-07-08": "OC"},
    )
    # Overtime worked during the on-call day
    test_client.post(
        f"/admin/substitutes/{sub.id}/overtime/add",
        data={"date": "2026-07-08", "start_time": "14:00", "end_time": "22:30", "hours": "8.5"},
    )

    summaries = build_substitute_month_summaries(2026, 7, test_db)
    summary = next(s for s in summaries if s["substitute_id"] == sub.id)

    # Standby reduced by the overtime hours: 24 - 8.5 = 15.5
    assert summary["oncall_hours"] == 15.5
    assert summary["ot_hours"] == 8.5

    # The on-call day is displayed as OT in the month grid
    ot_day = next(d for d in summary["days"] if d["date"] == date(2026, 7, 8))
    assert ot_day["shift"].code == "OT"
    assert ot_day["hours"] == 8.5

    # Report row reflects the same figures
    row = next(r for r in build_month_report(2026, 7, test_db) if r["substitute_id"] == sub.id)
    assert row["oncall_hours"] == 15.5
    assert row["ot_hours"] == 8.5


def test_report_requires_admin(test_client, test_db, test_user):
    """A non-admin user must not reach the report."""
    token = create_access_token(data={"sub": str(test_user.id)})
    test_client.cookies.set("access_token", token)
    r = test_client.get("/admin/report?year=2026&month=7", follow_redirects=False)
    assert r.status_code in (302, 303, 401, 403)
