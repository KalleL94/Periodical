"""The detailed breakdown table renders in the range view, same as in the month view."""

from app.auth.auth import create_access_token


def _login(client, user):
    client.cookies.set("access_token", f"Bearer {create_access_token(data={'sub': str(user.id)})}")


def test_range_view_shows_breakdown_table(test_client, admin_user):
    _login(test_client, admin_user)
    r = test_client.get("/range/1?weeks=3&from=2026-07-01")
    assert r.status_code == 200
    assert 'id="breakdown-section"' in r.text
    assert "B.Storhelg(192)" in r.text
    # CSV export stays a month-view feature
    assert 'id="breakdown-csv"' not in r.text


def test_month_view_still_shows_breakdown_with_csv(test_client, admin_user):
    _login(test_client, admin_user)
    r = test_client.get("/month/1?year=2026&month=7")
    assert r.status_code == 200
    assert 'id="breakdown-section"' in r.text
    assert 'id="breakdown-csv"' in r.text


def test_range_breakdown_matches_month_breakdown(test_db):
    """A range covering exactly one month must produce the same rows as the month view."""
    from datetime import date

    from app.core.schedule.summary import build_range_breakdown_days, summarize_month_for_person

    month = summarize_month_for_person(2026, 7, 1, session=test_db, fetch_tax_table=False)
    rng = build_range_breakdown_days(date(2026, 7, 1), date(2026, 7, 31), 1, session=test_db)

    key = lambda d: (d["date"], d["hours"], sorted(d["ob_hours"].items()), d["ot_hours"])  # noqa: E731
    assert [key(d) for d in rng] == [key(d) for d in month["days"]]
