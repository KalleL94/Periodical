"""Month Excel export: headers and OB columns per employer profile."""

import datetime
from types import SimpleNamespace

import pytest

import app.routes.excel_shared as xs
from app.routes.excel_shared import (
    COL_HEADERS,
    EXCEL_HEADERS_BY_PROFILE,
    REPORT_COL_HEADERS,
    _build_month_rows,
)


def _summary(ob_hours, oncall=None):
    shift = SimpleNamespace(code="N1", label="Dag", start_time="08:00", end_time="17:00")
    day = {
        "date": datetime.date(2026, 6, 1),
        "shift": shift,
        "start": "08:00",
        "end": "17:00",
        "ob_hours": ob_hours,
        "hours": 9,
        "ot_hours": 0,
        "oncall_details": {"breakdown": oncall} if oncall else None,
    }
    return {"days": [day]}


def test_nexer_ob3_and_ob4_get_separate_columns():
    headers, rows, totals = _build_month_rows(
        _summary({"OB3": 3, "OB4": 2}), 2026, 6, EXCEL_HEADERS_BY_PROFILE["nexer"], split_ob4=True
    )
    assert headers[headers.index("OB Helg") + 1] == "OB Helgdag"
    first = rows[0]
    assert first[headers.index("OB Helg")] == 3.0
    assert first[headers.index("OB Helgdag")] == 2.0
    assert totals[headers.index("OB Helgdag")] == 2.0


def test_nexer_oncall_lands_in_its_own_columns():
    oc = {"NX_NIGHT": {"hours": 4, "pay": 0}, "NX_WEEKDAY": {"hours": 1, "pay": 0}}
    headers, rows, _ = _build_month_rows(_summary({}, oc), 2026, 6, EXCEL_HEADERS_BY_PROFILE["nexer"], split_ob4=True)
    assert rows[0][headers.index("Beredskap Natt")] == 4.0
    assert rows[0][headers.index("Beredskap Vardag")] == 1.0


def test_default_still_merges_ob3_and_ob4():
    headers, rows, _ = _build_month_rows(_summary({"OB3": 3, "OB4": 2}), 2026, 6)
    assert rows[0][headers.index("OB tillägg helg(x1,24)")] == 5.0


def test_header_tables():
    assert EXCEL_HEADERS_BY_PROFILE["agiremus"] == COL_HEADERS
    assert EXCEL_HEADERS_BY_PROFILE["ica"] == REPORT_COL_HEADERS
    assert "Beredskap Natt" in EXCEL_HEADERS_BY_PROFILE["nexer"]
    assert "Kväll 150" not in EXCEL_HEADERS_BY_PROFILE["experis"]
    assert "OB Kväll" in EXCEL_HEADERS_BY_PROFILE["experis"]


@pytest.mark.parametrize(
    "profile,headers_key,split",
    [
        ("nexer", "nexer", True),
        ("ica", "ica", False),
        ("agiremus", "agiremus", False),
        ("experis", "experis", False),
        # An unknown stored profile falls back to the ICA headers.
        ("bogus", "ica", False),
    ],
)
def test_personal_export_picks_profile_headers(
    test_client, test_db, test_user, monkeypatch, profile, headers_key, split
):
    from app.core.schedule import clear_schedule_cache
    from app.database.database import User

    test_db.query(User).filter_by(id=1).update({"employer_profile": profile})
    test_db.commit()
    clear_schedule_cache()
    captured = {}
    original = xs.populate_month_sheet

    def _spy(ws, summary, year, month, *args, **kwargs):
        captured.update(kwargs)
        return original(ws, summary, year, month, *args, **kwargs)

    monkeypatch.setattr(xs, "populate_month_sheet", _spy)
    test_client.post("/login", data={"username": "testuser", "password": "testpass123"})
    r = test_client.get("/month/1/export-excel?year=2026&month=6")
    assert r.status_code == 200
    assert captured["headers"] == EXCEL_HEADERS_BY_PROFILE[headers_key]
    assert captured["split_ob4"] is split


def test_admin_report_headers_unchanged():
    assert REPORT_COL_HEADERS[6:10] == ["Kväll 150", "Natt 151", "Helg 152", "Storhelg 153"]
    assert len(REPORT_COL_HEADERS) == len(COL_HEADERS) == 16
    headers, rows, _ = _build_month_rows(
        _summary({"OB3": 3, "OB4": 2}), 2026, 6, REPORT_COL_HEADERS, split_oncall_overtime=False
    )
    assert rows[0][headers.index("Helg 152")] == 5.0
