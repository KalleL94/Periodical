"""Tests for the payslip PDF parser.

The rate and categorisation tests run everywhere. The tests that read the real
payslips only run locally: those files carry personal data and stay out of the
repo, so an anonymised fixture covers the parse path in CI instead.
"""

import datetime
import pathlib

import pytest

from app.core.payslip_import import categorize, parse_payslip_pdf

# Two real users with different rates configured (see User.custom_rates).
RATES_LOW = {"ob": {"OB1": 25.52, "OB2": 38.28, "OB3": 51.03, "OB4": 51.03, "OB5": 99.94}, "ot": 425.28}
RATES_HIGH = {"ob": {"OB1": 71.0, "OB2": 106.0, "OB3": 141.0, "OB4": 141.0, "OB5": 281.0}, "ot": 820.0}

SAMPLES = pathlib.Path(__file__).resolve().parents[1] / "temp" / "Lönespec"
MONTHS = ["202510", "202511", "202512", "202601", "202602", "202603", "202604", "202605", "202606"]

FIXTURE_PDF = pathlib.Path(__file__).resolve().parent / "fixtures" / "payslip_202606.pdf"

# Applied per test rather than to the module, so the rate and categorisation
# tests still run in CI where the real payslips are absent.
needs_samples = pytest.mark.skipif(not SAMPLES.is_dir(), reason="sample payslips not available")


def parse(name: str) -> dict:
    return parse_payslip_pdf((SAMPLES / f"{name}.pdf").read_bytes())


def test_anonymised_fixture_parses_end_to_end():
    """The one PDF that is committed, so CI exercises the real parse path."""
    result = parse_payslip_pdf(FIXTURE_PDF.read_bytes())

    assert result["period"] == (2026, 6)
    assert result["gross"] == 42583.99
    assert result["tax"] == -9250.0
    assert result["net"] == 33333.99
    assert result["tax_table"] == "33:1"

    assert [(row.label, row.category) for row in result["rows"]] == [
        ("Månadslön", "base"),
        ("Övertid betald 100%, timlön", "ot"),
        ("Beredskap varrdag 75kr", "oc_vardag"),
        ("Faktor 1,24", "OB3"),
        ("OB Vardag kväll", "OB1"),
        ("Sjuklön dag -14, månadslön", "sick_pay"),
        ("Sjukavdrag 100%, månadslön", "sick_deduction"),
        ("Karensavdrag", "karens"),
    ]
    assert round(sum(row.amount for row in result["rows"]), 2) == result["gross"]


@needs_samples
def test_minimal_payslip():
    result = parse("202510")
    assert result["period"] == (2025, 10)
    assert result["gross"] == 37000.0
    assert result["tax"] == -8242.0
    assert result["net"] == 30758.0
    assert result["tax_table"] == "34:1"

    labels = [(row.label, row.amount, row.category) for row in result["rows"]]
    assert labels == [
        ("Månadslön", 37000.0, "base"),
        ("Friskvårdsersättning", 2000.0, "expense"),
    ]


@needs_samples
def test_full_payslip_with_deductions():
    rows = parse("202606")["rows"]
    assert len(rows) == 11

    by_category = {row.category: row for row in rows}
    assert by_category["vacation_deduction"].amount == -6808.0
    assert by_category["sick_deduction"].amount == -3415.36
    assert by_category["karens"].amount == -1366.15
    assert by_category["vacation_pay"].amount == 7444.4
    assert by_category["sick_pay"].amount == 2732.32

    overtime = by_category["ot"]
    assert overtime.from_date == datetime.date(2026, 6, 1)
    assert overtime.to_date == datetime.date(2026, 6, 30)
    assert (overtime.qty, overtime.unit, overtime.unit_price) == (8.0, "tim", 422.86)

    # Karensavdrag has neither quantity nor á-price, only a sum.
    assert by_category["karens"].qty is None
    assert by_category["karens"].unit_price is None


@pytest.mark.parametrize("name", MONTHS)
@needs_samples
def test_rows_add_up_to_gross(name):
    """Tax free expense reimbursements are paid out but are not part of the gross pay."""
    result = parse(name)
    taxable = sum(row.amount for row in result["rows"] if row.category != "expense")
    assert round(taxable, 2) == result["gross"]


@pytest.mark.parametrize("name", MONTHS)
@needs_samples
def test_no_unknown_rows(name):
    unknown = [row.label for row in parse(name)["rows"] if row.category == "unknown"]
    assert unknown == []


@pytest.mark.parametrize("name", MONTHS)
@needs_samples
def test_period_matches_filename(name):
    assert parse(name)["period"] == (int(name[:4]), int(name[4:]))


def test_same_row_categorized_by_the_users_own_rates():
    """An unrecognised label falls back to the á-price, which means different rates differ."""
    row = ("Tillägg enligt lokalt avtal", 71.0)
    assert categorize(*row) == "unknown"  # 71,00 is not in the built-in hint table
    assert categorize(*row, rates=RATES_HIGH) == "OB1"
    assert categorize(*row, rates=RATES_LOW) == "unknown"

    other = ("Tillägg enligt lokalt avtal", 25.52)
    assert categorize(*other, rates=RATES_LOW) == "OB1"
    # Falls through to the built-in hints, which still know this rate as OB1.
    assert categorize(*other, rates=RATES_HIGH) == "OB1"


def test_rate_match_tolerates_ore_rounding_and_ambiguity():
    assert categorize("Okänt tillägg", 70.996, rates=RATES_HIGH) == "OB1"
    assert categorize("Okänt tillägg", 71.01, rates=RATES_HIGH) == "OB1"
    assert categorize("Okänt tillägg", 71.5, rates=RATES_HIGH) == "unknown"
    # OB3 and OB4 share a rate: the lower level wins deterministically.
    assert categorize("Okänt tillägg", 141.0, rates=RATES_HIGH) == "OB3"


def test_oncall_rates_map_to_categories():
    rates = {"oncall": {"OC_WEEKDAY": 80.0, "OC_WEEKEND": 105.0, "OC_SPECIAL": 200.0}}
    assert categorize("Okänd post X", 80.0, rates) == "oc_vardag"
    assert categorize("Okänd post X", 105.0, rates) == "oc_helg"
    assert categorize("Okänd post X", 200.0, rates) == "oc_storhelg"


@needs_samples
def test_rates_do_not_override_label_matching():
    """Known labels win over á-prices, even when a rate happens to collide."""
    rows = parse("202606")["rows"]
    with_rates = parse_payslip_pdf((SAMPLES / "202606.pdf").read_bytes(), rates=RATES_HIGH)["rows"]
    assert [r.category for r in rows] == [r.category for r in with_rates]
