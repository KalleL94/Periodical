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
        # The Excel export must stay untouched for the positions that have it.
        assert "export-excel" in html or test_user.id not in (6, 8)

        # Optional dump for manual browser verification of the JS export.
        dump = os.environ.get("BREAKDOWN_HTML_DUMP")
        if dump:
            with open(dump, "w", encoding="utf-8") as fh:
                fh.write(html)
