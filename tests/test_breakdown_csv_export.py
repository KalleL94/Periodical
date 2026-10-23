"""Breakdown CSV export button on the personal month view."""

import os


class TestBreakdownCsvExport:
    def test_month_view_offers_csv_export(self, test_client, test_user):
        """The detailed breakdown must expose a CSV export button for salary viewers."""
        test_client.post("/login", data={"username": "testuser", "password": "testpass123"})

        response = test_client.get(f"/month/{test_user.id}?year=2026&month=6")

        assert response.status_code == 200
        html = response.text
        assert 'id="breakdown-csv"' in html
        assert "exportBreakdownCsv" in html
        # The Excel export is offered to every salary viewer.
        assert "export-excel" in html

        # Optional dump for manual browser verification of the JS export.
        dump = os.environ.get("BREAKDOWN_HTML_DUMP")
        if dump:
            with open(dump, "w", encoding="utf-8") as fh:
                fh.write(html)


def _html_for(client, test_db, profile):
    from app.core.schedule import clear_schedule_cache
    from app.database.database import User

    test_db.query(User).filter_by(id=1).update({"employer_profile": profile})
    test_db.commit()
    clear_schedule_cache()
    client.post("/login", data={"username": "testuser", "password": "testpass123"})
    r = client.get("/month/1?year=2026&month=6")
    assert r.status_code == 200
    return r.text


def test_format_dropdown_offers_both_formats(test_client, test_db, test_user):
    html = _html_for(test_client, test_db, "ica")
    assert 'id="breakdown-csv-format"' in html
    assert 'value="sv"' in html and 'value="std"' in html
    assert "breakdownCsvFormat" in html


def _headers(page):
    import html as htmllib
    import json
    import re

    raw = re.search(r"data-headers='(.*?)'", page, re.S).group(1)
    return json.loads(htmllib.unescape(raw))


def test_ica_headers(test_client, test_db, test_user):
    headers = _headers(_html_for(test_client, test_db, "ica"))
    assert headers[:6] == ["Datum", "Veckodag", "Passtyp", "Start", "Slut", "Vanliga timmar"]
    assert "Kväll 150" in headers and "Storhelg 153" in headers
    assert headers[-1] == "Övertid"


def test_agiremus_headers(test_client, test_db, test_user):
    assert "OB4(x1,24)" in _headers(_html_for(test_client, test_db, "agiremus"))


def test_nexer_headers(test_client, test_db, test_user):
    headers = _headers(_html_for(test_client, test_db, "nexer"))
    assert "Beredskap Natt" in headers and "OB Helgdag" in headers
    assert not any("150" in h for h in headers)
