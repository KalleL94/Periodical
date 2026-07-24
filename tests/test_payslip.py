"""Tests for the payslip row builder, overrides and upload comparison.

These cover app/core/schedule/payslip.py directly with synthetic month data, so
they fail on a logic change without needing a rotation or a seeded schedule.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.payslip_import import parse_payslip_pdf
from app.core.schedule.payslip import (
    Payslip,
    PayslipRow,
    add_vacation_row,
    apply_payslip_overrides,
    build_payslip_rows,
    compare_to_upload,
)

# Anonymised fixture, committed so CI has test data. See
# tests/fixtures/make_payslip_fixture.py for how it is generated.
FIXTURE_PDF = Path(__file__).resolve().parent / "fixtures" / "payslip_202606.pdf"

# The real payslips carry personal data and live in gitignored temp/. Tests
# using them run locally and skip everywhere else.
PAYSLIP_DIR = Path(__file__).resolve().parent.parent / "temp" / "Lönespec"
needs_real_payslips = pytest.mark.skipif(not PAYSLIP_DIR.is_dir(), reason="real payslips are not in the repo")


def _day(code="N2", hours=8.5, ob_hours=None, ob_pay=None, oncall=None, ot_hours=0.0, ot_pay=0.0):
    """One day in the shape summarize_month_for_person produces."""
    return {
        "shift": SimpleNamespace(code=code, label=code),
        "hours": hours,
        "ob_hours": ob_hours or {},
        "ob_pay": ob_pay or {},
        "oncall_details": {"breakdown": oncall} if oncall else {},
        "ot_hours": ot_hours,
        "ot_pay": ot_pay,
    }


def _totals(**kwargs):
    base = {"substitute_base_pay": 0.0, "substitute_hours": 0.0, "sick_ob_pay": 0.0, "absence_details": []}
    base.update(kwargs)
    return base


def test_rows_sum_to_the_month_gross():
    """The payslip must reconcile to gross pay, or the two views disagree."""
    days = [
        _day(ob_hours={"OB1": 4.0}, ob_pay={"OB1": 100.0}),
        _day(ob_hours={"OB2": 6.0}, ob_pay={"OB2": 300.0}),
        _day(code="OC", hours=0.0, oncall={"OC_WEEKDAY": {"hours": 24.0, "pay": 1800.0}}),
        _day(code="OT", hours=0.0, ot_hours=8.0, ot_pay=3382.88),
    ]
    slip = build_payslip_rows(_totals(), days, base_salary=37000.0, is_hourly=False, year=2026, month=6)

    # Gross for a monthly-wage user is base + OB supplements + on-call + overtime.
    expected_gross = 37000.0 + 100.0 + 300.0 + 1800.0 + 3382.88
    assert round(slip.total, 2) == round(expected_gross, 2)
    assert slip.by_key()["base"].amount == 37000.0
    assert slip.by_key()["OB2"].qty == 6.0
    assert slip.period == "20260601-20260630"


def test_ob_hours_do_not_leak_into_normal_hours():
    """Normal hours are worked hours minus OB hours, never the full shift."""
    days = [_day(hours=8.0, ob_hours={"OB1": 3.0}, ob_pay={"OB1": 75.0})]
    slip = build_payslip_rows(_totals(), days, base_salary=34666.0, is_hourly=True, year=2026, month=6)

    rows = slip.by_key()
    assert rows["norm"].qty == 5.0
    assert rows["OB1"].qty == 3.0


def test_absence_deduction_splits_per_type_and_is_negative():
    days = [_day()]
    totals = _totals(
        absence_details=[
            {"type": "SICK", "deduction": 2049.22, "hours": 16.0},
            {"type": "VAB", "deduction": 853.84, "hours": 8.0},
        ]
    )
    slip = build_payslip_rows(totals, days, base_salary=37000.0, is_hourly=False, year=2026, month=6)

    rows = slip.by_key()
    assert rows["sick_deduction"].amount == -2049.22
    assert rows["vab_deduction"].amount == -853.84


def test_override_returns_the_per_row_delta_applied_to_gross():
    """The deltas are what summarize adds to brutto_pay, so they must be exact."""
    days = [_day(ot_hours=8.0, ot_pay=3000.0, code="OT", hours=0.0)]
    slip = build_payslip_rows(_totals(), days, base_salary=37000.0, is_hourly=False, year=2026, month=6)

    deltas = apply_payslip_overrides(slip, {"ot": {"amount": 3382.88, "hours": 8.0, "reason": "enligt lönespec"}})

    assert round(deltas["ot"], 2) == 382.88
    row = slip.by_key()["ot"]
    assert row.overridden is True
    assert row.computed_amount == 3000.0
    assert row.amount == 3382.88
    # The total must move with the override, not just the single row.
    assert round(slip.total, 2) == round(37000.0 + 3382.88, 2)


def test_override_can_add_a_row_the_model_never_computes():
    """An employer may pay something this app has no rule for at all."""
    slip = build_payslip_rows(_totals(), [_day()], base_salary=37000.0, is_hourly=False, year=2026, month=6)

    deltas = apply_payslip_overrides(slip, {"skiftbonus": {"amount": 1500.0}})

    assert deltas["skiftbonus"] == 1500.0
    assert slip.by_key()["skiftbonus"].amount == 1500.0
    # Unknown keys sort last so the known rows keep their payslip order.
    assert slip.rows[-1].key == "skiftbonus"


def test_deduction_override_routes_into_the_absence_deduction_total():
    """A sick-deduction override must move the itemised figure the month and
    year views display (absence_deduction), not only gross pay."""
    from app.core.schedule.payslip import route_override_deltas

    totals = {"absence_deduction": 2454.85, "sick_ob_pay": 500.0}
    # Computed -2454.85, overridden to -2049.0: less deducted by 405.85.
    route_override_deltas(totals, {"sick_deduction": 405.85})

    assert round(totals["absence_deduction"], 2) == 2049.0
    # An untouched field stays put.
    assert totals["sick_ob_pay"] == 500.0


def test_sick_rows_share_one_comparison_bucket():
    """An employer splits sick leave into three lines where this app nets one.

    Without the bucket grouping every sick month would report false diffs.
    """
    slip = Payslip(period="20260601-20260630")
    slip.rows = [
        PayslipRow(key="sick_pay", amount=2732.32),
        PayslipRow(key="sick_deduction", amount=-4781.51),
        PayslipRow(key="base", amount=37000.0),
    ]

    buckets = slip.bucket_totals()
    assert round(buckets["sick"], 2) == -2049.19
    assert buckets["base"] == 37000.0


def test_ob3_and_ob4_compare_as_one_wage_code_152_line():
    """The payslip lists OB weekend and OB public holiday as one line (code 152).

    The app splits them into OB3 and OB4, so without merging them a correct
    month reports +383 on one and -383 on the other.
    """
    slip = Payslip(period="20260601-20260630")
    slip.rows = [
        PayslipRow(key="OB3", amount=536.0),
        PayslipRow(key="OB4", amount=383.0),
    ]
    # The payslip has the pair as one "Faktor 1,24" line, which the parser
    # categorises as OB3 (the lower level when the two rates coincide).
    uploaded = [SimpleNamespace(category="OB3", amount=919.0)]

    result = compare_to_upload(slip, uploaded)
    lines = {line["bucket"]: line for line in result["lines"]}

    assert set(lines) == {"ob_152"}
    assert lines["ob_152"]["computed"] == 919.0
    assert lines["ob_152"]["uploaded"] == 919.0
    assert lines["ob_152"]["matched"] is True


def test_amount_diff_with_matching_hours_is_a_rate_difference():
    """Overtime: 8.0 tim both sides, but the a-price differs by 2.42/tim.

    The comparison must expose the quantity and unit price so the amount diff
    reads as a rate difference, not an unexplained number.
    """
    slip = Payslip(period="20260601-20260630")
    slip.rows = [PayslipRow(key="ot", qty=8.0, unit="tim", amount=3402.24)]
    uploaded = [SimpleNamespace(category="ot", qty=8.0, unit="tim", amount=3382.88)]

    line = compare_to_upload(slip, uploaded)["lines"][0]

    assert line["computed_qty"] == 8.0
    assert line["uploaded_qty"] == 8.0
    assert line["qty_mismatch"] is False
    assert round(line["computed_price"], 2) == 425.28
    assert round(line["uploaded_price"], 2) == 422.86
    assert line["matched"] is False  # the amount still differs


def test_quantity_mismatch_is_flagged_even_when_amounts_are_close():
    """Same amount, different hours (a rate error hiding a quantity error)."""
    slip = Payslip(period="20260601-20260630")
    slip.rows = [PayslipRow(key="ot", qty=8.0, unit="tim", amount=3400.0)]
    uploaded = [SimpleNamespace(category="ot", qty=10.0, unit="tim", amount=3400.0)]

    line = compare_to_upload(slip, uploaded)["lines"][0]

    assert line["qty_mismatch"] is True
    assert line["matched"] is False


def test_mixed_sign_bucket_hides_meaningless_quantity():
    """Vacation nets a supplement and a deduction: summing their days is noise."""
    slip = Payslip(period="20260601-20260630")
    slip.rows = [PayslipRow(key="vacation_pay", qty=4.0, unit="dgr", amount=888.0)]
    uploaded = [
        SimpleNamespace(category="vacation_pay", qty=4.0, unit="dgr", amount=7444.4),
        SimpleNamespace(category="vacation_deduction", qty=4.0, unit="dgr", amount=-6808.0),
    ]

    line = compare_to_upload(slip, uploaded)["lines"][0]

    # The computed side has one row, so it keeps its quantity; the uploaded side
    # mixes signs, so its quantity is suppressed rather than summed to 8 days.
    assert line["computed_qty"] == 4.0
    assert line["uploaded_qty"] is None
    assert round(line["uploaded"], 2) == 636.40


def test_compare_against_an_uploaded_pdf():
    """End to end: parse a payslip PDF and diff it against computed rows."""
    parsed = parse_payslip_pdf(FIXTURE_PDF.read_bytes())

    slip = Payslip(period="20260601-20260630")
    slip.rows = [
        PayslipRow(key="base", amount=37000.0),
        PayslipRow(key="oc_vardag", qty=40.0, unit="tim", amount=3000.0),
        # This month's overtime was computed 400 kr too low.
        PayslipRow(key="ot", qty=8.0, unit="tim", amount=2982.88),
    ]

    result = compare_to_upload(slip, parsed["rows"])
    lines = {line["bucket"]: line for line in result["lines"]}

    assert lines["base"]["matched"] is True
    assert lines["oc_vardag"]["matched"] is True
    assert lines["ot"]["matched"] is False
    assert round(lines["ot"]["diff"], 2) == 400.00
    # Sick leave is three lines on the payslip and one net figure here, so it
    # must land in a single bucket rather than three separate diffs.
    assert round(lines["sick"]["uploaded"], 2) == -2049.19
    assert lines["sick"]["missing_here"] is True
    assert result["unknown_rows"] == []


@needs_real_payslips
def test_real_payslip_shows_the_vacation_supplement_diff():
    """The employer pays 159.10 per vacation day where Handels 9 gives 296.00.

    This is a real discrepancy in a real payslip, kept as a regression guard on
    the comparison actually surfacing it.
    """
    parsed = parse_payslip_pdf((PAYSLIP_DIR / "202606.pdf").read_bytes())

    # A computed month that deliberately gets the vacation supplement wrong.
    slip = Payslip(period="20260601-20260630")
    slip.rows = [
        PayslipRow(key="base", amount=37000.0),
        PayslipRow(key="vacation_pay", amount=1184.0),
    ]

    result = compare_to_upload(slip, parsed["rows"])
    lines = {line["bucket"]: line for line in result["lines"]}

    assert lines["base"]["matched"] is True
    # Agiremus pays 159.10 per vacation day, the model assumes 296.00 (0.8%).
    vacation = lines["vacation"]
    assert vacation["matched"] is False
    assert round(vacation["uploaded"], 2) == 636.40
    assert round(vacation["diff"], 2) == -547.60
    # Overtime is on the payslip but missing from this computed month.
    assert lines["ot"]["missing_here"] is True
    assert result["unknown_rows"] == []


@needs_real_payslips
def test_tax_free_expenses_are_excluded_from_the_comparison():
    """Utlägg and friskvård are reimbursements, not pay, and never reach gross."""
    parsed = parse_payslip_pdf((PAYSLIP_DIR / "202510.pdf").read_bytes())

    slip = Payslip(period="20251001-20251031")
    slip.rows = [PayslipRow(key="base", amount=37000.0)]

    result = compare_to_upload(slip, parsed["rows"])
    buckets = {line["bucket"] for line in result["lines"]}

    assert "expense" not in buckets
    assert all(line["matched"] for line in result["lines"])


def test_vacation_row_is_added_once():
    """add_vacation_row runs outside summarize, so it must be idempotent."""
    slip = build_payslip_rows(_totals(), [_day()], base_salary=37000.0, is_hourly=False, year=2026, month=6)

    add_vacation_row(slip, 1184.0, 4)
    add_vacation_row(slip, 1184.0, 4)

    vacation_rows = [r for r in slip.rows if r.key == "vacation_pay"]
    assert len(vacation_rows) == 1
    assert vacation_rows[0].qty == 4
    assert round(slip.total, 2) == round(37000.0 + 1184.0, 2)
