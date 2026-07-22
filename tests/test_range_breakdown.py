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


def _pay_row_cells(html):
    import re

    row = re.search(r'<tr class="breakdown-pay-row">(.*?)</tr>', html, re.S).group(1)
    return [re.sub(r"<[^>]+>|\s", "", c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]


def test_breakdown_shows_amount_per_compensation_column(test_client, admin_user, test_db):
    """The amount row sums the pay already computed per day, per wage code."""
    import datetime

    from app.core.schedule import clear_schedule_cache
    from app.core.schedule.summary import summarize_month_for_person
    from app.database.database import OvertimeShift

    # Own the schedule this asserts on rather than relying on where the rotation
    # happens to land: a Saturday night shift guarantees both OB and overtime pay.
    test_db.add(
        OvertimeShift(
            user_id=1,
            date=datetime.date(2026, 7, 4),
            start_time=datetime.time(22, 0),
            end_time=datetime.time(2, 0),
            hours=4.0,
            ot_pay=0.0,
            is_extension=False,
        )
    )
    test_db.commit()
    clear_schedule_cache()

    _login(test_client, admin_user)
    r = test_client.get("/month/1?year=2026&month=7")
    assert r.status_code == 200

    month = summarize_month_for_person(2026, 7, 1, session=test_db, fetch_tax_table=False)
    expected = {code: round(pay) for code, pay in month["ob_pay"].items() if round(pay)}
    assert expected, "the overtime shift above must produce OB pay, or this asserts nothing"

    cells = _pay_row_cells(r.text)
    ob_cells = dict(zip(["OB1", "OB2", "OB3", "OB4", "OB5"], cells[6:11], strict=True))
    assert {c: int(v) for c, v in ob_cells.items() if v} == expected
    assert int(cells[15]) == round(month["ot_pay"])

    # ...and the same row renders in the range view
    r2 = test_client.get("/range/1?weeks=3&from=2026-07-01")
    assert 'class="breakdown-pay-row"' in r2.text
