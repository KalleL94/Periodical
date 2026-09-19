"""No amount in kronor may reach a viewer without salary permission.

The month and year summaries name the same figures differently (brutto_pay versus
total_brutto), and while two hand-maintained strip functions covered a subset each,
the year totals, on-call pay, overtime pay and every deduction rendered in full to
anyone who opened another person's /year, /statistics or /month.

These tests are shaped around the templates: whatever year.html, statistics.html and
month.html read out of a summary is what has to come back blank.
"""

import re
from pathlib import Path

import pytest

from app.core.helpers import strip_salary_data, strip_year_summary

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"

# Rendered figures that are amounts in kronor. Hour and day counts stay visible:
# the schedule itself is shared, so they carry nothing the viewer cannot already see.
YEAR_MONEY = [
    "total_brutto",
    "total_netto",
    "total_ob",
    "avg_brutto",
    "avg_netto",
    "avg_ob",
    "total_oncall",
    "avg_oncall",
    "total_ot",
    "avg_ot",
    "total_absence_deduction",
    "avg_absence_deduction",
    "sick_deduction",
    "vab_deduction",
    "leave_deduction",
    "off_deduction",
    "total_vacation_supplement",
    "avg_vacation_supplement",
    "total_sick_ob_pay",
    "avg_sick_ob_pay",
    "total_sick_ob_lost",
    "total_sick_total_ob",
    "avg_sick_total_ob",
]

MONTH_MONEY = [
    "brutto_pay",
    "netto_pay",
    "tax",
    "base_salary",
    "total_ob",
    "oncall_pay",
    "ot_pay",
    "absence_deduction",
    "vacation_supplement",
    "sick_ob_pay",
    "sick_total_ob",
    "transition_direct",
]

MONEY_MAPS = ["ob_pay", "ob_hours", "ob_pay_by_code", "ob_hours_by_code", "override_deltas"]


@pytest.mark.parametrize("key", YEAR_MONEY)
def test_year_summary_hides_every_amount(key):
    summary = dict.fromkeys(YEAR_MONEY, 123456.0) | {"total_hours": 1600.0, "total_shifts": 210}
    stripped = strip_year_summary(summary)

    assert stripped[key] is None, f"{key} still carries an amount"
    # The schedule side is not salary data and must survive.
    assert stripped["total_hours"] == 1600.0
    assert stripped["total_shifts"] == 210


@pytest.mark.parametrize("key", MONTH_MONEY)
def test_month_summary_hides_every_amount(key):
    summary = dict.fromkeys(MONTH_MONEY, 42000.0) | {"total_hours": 160.0, "num_shifts": 20}
    stripped = strip_salary_data(summary)

    assert stripped[key] is None, f"{key} still carries an amount"
    assert stripped["total_hours"] == 160.0
    assert stripped["num_shifts"] == 20


@pytest.mark.parametrize("key", MONEY_MAPS)
def test_code_keyed_amounts_come_back_empty(key):
    """Templates read these with .get(code, 0), so they have to stay dicts."""
    assert strip_year_summary({key: {"OB1": 900.0}})[key] == {}
    assert strip_salary_data({key: {"OB1": 900.0}})[key] == {}


def test_per_day_ob_is_stripped_from_a_month_summary():
    stripped = strip_salary_data(
        {"days": [{"date": "2026-03-02", "shift": "D", "ob_pay": {"OB1": 500.0}, "ob_hours": {"OB1": 4.0}}]}
    )
    day = stripped["days"][0]

    assert day["ob_pay"] == {}
    assert day["ob_hours"] == {}
    assert day["shift"] == "D", "the schedule itself must survive stripping"


def test_stripping_does_not_mutate_the_caller_s_dict():
    original = {"total_brutto": 500000.0, "ob_pay_by_code": {"OB1": 900.0}}
    strip_year_summary(original)

    assert original["total_brutto"] == 500000.0
    assert original["ob_pay_by_code"] == {"OB1": 900.0}


def test_every_money_figure_the_year_templates_render_is_covered():
    """Guards the list above against a new pay figure being added to a template.

    A template reading year_summary.<something>_pay or a new total_* amount without
    that key reaching _MONEY_SCALARS is exactly how the leak happened the first time.
    """
    rendered = set()
    for name in ("year.html", "statistics.html"):
        rendered |= set(re.findall(r"year_summary\.([a-z_]+)", (TEMPLATES / name).read_text()))

    # Anything named like an amount has to be in the stripped set.
    money_shaped = {k for k in rendered if re.search(r"(brutto|netto|_ob$|_pay|deduction|supplement|_ot$)", k)}
    # Blank is None for a scalar and {} for a code-keyed mapping.
    stripped = strip_year_summary(dict.fromkeys(rendered, 1.0))
    missing = {k for k in money_shaped if stripped[k] not in (None, {})}

    assert not missing, f"templates render these amounts unstripped: {sorted(missing)}"
