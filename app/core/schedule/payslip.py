"""Payslip rows for one month.

Lays the month's figures out the way a Swedish payslip does: one row per
compensation type with quantity, unit price and amount. The rows are built from
the same totals the month view already shows, so the payslip can never drift
from the month summary.

Row keys are shared with app/core/payslip_import.py so an uploaded payslip can
be compared category by category.
"""

from dataclasses import dataclass, field

from app.core.schedule.wages import _MONTHLY_HOURS

# On-call rule codes grouped the way a payslip lists them.
_OC_TO_GROUP = {
    "OC_WEEKDAY": "oc_vardag",
    "OC_WEEKEND": "oc_helg",
    "OC_WEEKEND_SAT": "oc_helg",
    "OC_WEEKEND_SUN": "oc_helg",
    "OC_WEEKEND_MON": "oc_helg",
    "OC_HOLIDAY": "oc_helgdag",
    "OC_HOLIDAY_EVE": "oc_helgdag",
    "OC_NATIONALDAGEN": "oc_helgdag",
    "OC_SPECIAL": "oc_storhelg",
}

# Row order on the rendered payslip. The translation key is resolved in the
# template so the labels stay in translations.py with all other user-facing text.
ROW_ORDER = (
    "base",
    "norm",
    "absence_pay",
    "OB1",
    "OB2",
    "OB3",
    "OB4",
    "OB5",
    "oc_vardag",
    "oc_helg",
    "oc_helgdag",
    "oc_storhelg",
    "ot",
    "substitute",
    "vacation_pay",
    "sick_pay",
    "sick_deduction",
    "vab_deduction",
    "leave_deduction",
)

# Rows an uploaded payslip splits differently from the way this app aggregates
# them. Both sides are summed per bucket before being compared, so a payslip
# that lists "Sjukavdrag", "Sjuklön" and "Karensavdrag" as three lines is not
# reported as three diffs against this app's single net sick deduction.
COMPARE_BUCKETS = {
    "sick_pay": "sick",
    "sick_deduction": "sick",
    "karens": "sick",
    "vacation_pay": "vacation",
    "vacation_deduction": "vacation",
}

# Units, matching what a Swedish payslip prints in the "Enhet" column.
UNIT_MONTH = "mån"
UNIT_HOURS = "tim"
UNIT_DAYS = "dgr"


@dataclass
class PayslipRow:
    """One line on the payslip. `amount` is signed: deductions are negative."""

    key: str
    qty: float | None = None
    unit: str | None = None
    amount: float = 0.0
    # Set when a manual override replaced the computed figure.
    overridden: bool = False
    computed_amount: float | None = None
    reason: str | None = None

    @property
    def unit_price(self) -> float | None:
        """Amount per unit, or None when the row has no quantity."""
        if not self.qty:
            return None
        return self.amount / self.qty


@dataclass
class Payslip:
    period: str
    rows: list[PayslipRow] = field(default_factory=list)
    total: float = 0.0

    def by_key(self) -> dict[str, PayslipRow]:
        return {r.key: r for r in self.rows}

    def bucket_totals(self) -> dict[str, float]:
        """Amounts summed per comparison bucket (see COMPARE_BUCKETS)."""
        out: dict[str, float] = {}
        for row in self.rows:
            bucket = COMPARE_BUCKETS.get(row.key, row.key)
            out[bucket] = out.get(bucket, 0.0) + row.amount
        return out


def _aggregate_days(days: list[dict]) -> dict[str, dict]:
    """Sum OB, on-call and overtime hours and pay across a month's days.

    This mirrors what the breakdown table renders, so the payslip's OB and
    on-call rows always agree with the per-day table above them.
    """
    agg = {
        k: {"hours": 0.0, "pay": 0.0}
        for k in ("norm", "OB1", "OB2", "OB3", "OB4", "OB5", *set(_OC_TO_GROUP.values()), "ot")
    }
    for d in days:
        shift = d.get("shift")
        hours = d.get("hours", 0.0) or 0.0
        ob_h = d.get("ob_hours", {}) or {}
        ob_p = d.get("ob_pay", {}) or {}

        # Normal hours are the worked hours that carry no OB supplement.
        if shift and getattr(shift, "code", None) not in ("OFF", "OC", "OT") and hours:
            norm = max(hours - sum(ob_h.values()), 0.0)
            agg["norm"]["hours"] += norm
            for code in ("OB1", "OB2", "OB3", "OB4", "OB5"):
                agg[code]["hours"] += ob_h.get(code, 0.0) or 0.0
                # OB pay is the supplement only, never the base hourly pay.
                agg[code]["pay"] += ob_p.get(code, 0.0) or 0.0

        oc_bd = (d.get("oncall_details") or {}).get("breakdown", {}) or {}
        for oc_code, group in _OC_TO_GROUP.items():
            entry = oc_bd.get(oc_code) or {}
            agg[group]["hours"] += entry.get("hours", 0.0) or 0.0
            agg[group]["pay"] += entry.get("pay", 0.0) or 0.0

        agg["ot"]["hours"] += d.get("ot_hours", 0.0) or 0.0
        agg["ot"]["pay"] += d.get("ot_pay", 0.0) or 0.0
    return agg


def build_payslip_rows(
    totals: dict,
    days: list[dict],
    base_salary: float,
    is_hourly: bool,
    year: int,
    month: int,
    vacation_supplement: float = 0.0,
    vacation_days: int = 0,
) -> Payslip:
    """Build the month's payslip rows from the summary totals.

    `totals` is the running totals dict inside summarize_month_for_person, and
    `days` its per-day output. Deduction rows carry a negative amount so the
    rows sum to gross pay.
    """
    import calendar as _cal

    last_day = _cal.monthrange(year, month)[1]
    slip = Payslip(period=f"{year}{month:02d}01-{year}{month:02d}{last_day:02d}")
    agg = _aggregate_days(days)

    def add(key: str, amount: float, qty: float | None = None, unit: str | None = None) -> None:
        # Sub-krona noise would render as empty rows priced at 0.
        if abs(amount) < 0.005 and not qty:
            return
        slip.rows.append(PayslipRow(key=key, qty=qty, unit=unit, amount=amount))

    if is_hourly:
        # Hourly users have no monthly base: worked hours are the base pay.
        # Every worked hour is paid, including the ones that also carry an OB
        # supplement, so this uses the same worked_hours the gross correction in
        # summarize_month_for_person uses rather than the OB-free hours in agg.
        # Pricing only the non-OB hours here silently loses OB hours x rate.
        hourly_rate = totals.get("hourly_rate") or (base_salary / _MONTHLY_HOURS)
        worked = totals.get("hourly_worked_hours")
        if worked is None:
            worked = agg["norm"]["hours"]
        add("norm", worked * hourly_rate, worked, UNIT_HOURS)

        # period.py zeroes out absence hours and _hourly_corrected_gross pays
        # them back before the absence deduction is subtracted, which is what
        # makes the sick-pay base come out right. Without a row for them the
        # payslip falls short by exactly absence_hours x rate.
        absent = totals.get("absence_hours", 0.0) or 0.0
        add("absence_pay", absent * hourly_rate, absent, UNIT_HOURS)
    else:
        add("base", base_salary, 1, UNIT_MONTH)

    for code in ("OB1", "OB2", "OB3", "OB4", "OB5"):
        add(code, agg[code]["pay"], agg[code]["hours"], UNIT_HOURS)
    for group in ("oc_vardag", "oc_helg", "oc_helgdag", "oc_storhelg"):
        add(group, agg[group]["pay"], agg[group]["hours"], UNIT_HOURS)
    add("ot", agg["ot"]["pay"], agg["ot"]["hours"], UNIT_HOURS)

    sub_pay = totals.get("substitute_base_pay", 0.0) or 0.0
    if sub_pay:
        add("substitute", sub_pay, totals.get("substitute_hours", 0.0) or 0.0, UNIT_HOURS)

    if vacation_supplement:
        add("vacation_pay", vacation_supplement, vacation_days or None, UNIT_DAYS)

    # Sick pay here is the OB compensation paid on sick days; the wage part of
    # sick pay is already netted inside absence_deduction (see
    # calculate_absence_deduction: it returns karens + 20% of the sick hours).
    add("sick_pay", totals.get("sick_ob_pay", 0.0) or 0.0)

    # Split the single absence deduction per absence type, so each type gets the
    # payslip row it deserves rather than one opaque lump.
    per_type: dict[str, dict] = {}
    for detail in totals.get("absence_details") or []:
        entry = per_type.setdefault(detail["type"], {"deduction": 0.0, "hours": 0.0})
        entry["deduction"] += detail.get("deduction", 0.0) or 0.0
        entry["hours"] += detail.get("hours", 0.0) or 0.0
    for absence_type, key in (("SICK", "sick_deduction"), ("VAB", "vab_deduction"), ("LEAVE", "leave_deduction")):
        entry = per_type.get(absence_type)
        if entry and entry["deduction"]:
            add(key, -entry["deduction"], entry["hours"], UNIT_HOURS)

    slip.total = sum(r.amount for r in slip.rows)
    return slip


def get_payslip_overrides(session, user_id: int, year: int, month: int) -> dict[str, dict]:
    """Manual per-row overrides for a month, keyed by payslip row key."""
    if session is None or user_id is None:
        return {}
    from app.database.database import PayslipOverride

    rows = (
        session.query(PayslipOverride)
        .filter(
            PayslipOverride.user_id == user_id,
            PayslipOverride.year == year,
            PayslipOverride.month == month,
        )
        .all()
    )
    return {r.row_key: {"amount": r.amount, "hours": r.hours, "reason": r.reason} for r in rows}


def apply_payslip_overrides(slip: Payslip, overrides: dict[str, dict]) -> float:
    """Replace computed rows with their manual overrides.

    Returns the net change to gross pay, which the caller adds to brutto_pay so
    the month, year and dashboard figures all follow the override.
    """
    if not overrides:
        return 0.0

    delta = 0.0
    existing = slip.by_key()

    for key, ov in overrides.items():
        amount = float(ov.get("amount") or 0.0)
        row = existing.get(key)
        if row is None:
            # An override for a row this app did not compute at all: a
            # compensation type the employer pays but the model does not know.
            row = PayslipRow(key=key, amount=0.0)
            slip.rows.append(row)
            existing[key] = row
        delta += amount - row.amount
        row.computed_amount = row.amount
        row.amount = amount
        row.overridden = True
        row.reason = ov.get("reason")
        if ov.get("hours") is not None:
            row.qty = float(ov["hours"])
            row.unit = row.unit or UNIT_HOURS

    # Keep the canonical order stable after appending unknown rows.
    slip.rows.sort(key=lambda r: ROW_ORDER.index(r.key) if r.key in ROW_ORDER else len(ROW_ORDER))
    slip.total = sum(r.amount for r in slip.rows)
    return delta


def add_vacation_row(slip: Payslip, supplement: float, days: int) -> None:
    """Add the vacation supplement row.

    The supplement is folded into gross pay outside summarize_month_for_person
    (see fold_vacation_supplement_into_pay), so it is added to the payslip at
    the same point rather than inside the month summary, where it would be
    counted twice.
    """
    if not supplement or any(r.key == "vacation_pay" for r in slip.rows):
        return
    slip.rows.append(PayslipRow(key="vacation_pay", qty=days or None, unit=UNIT_DAYS, amount=supplement))
    slip.rows.sort(key=lambda r: ROW_ORDER.index(r.key) if r.key in ROW_ORDER else len(ROW_ORDER))
    slip.total = sum(r.amount for r in slip.rows)


# Rounding slack when matching an uploaded payslip against the computed one.
# The employer rounds each line to ören, so exact equality would report noise.
MATCH_TOLERANCE = 1.0


def compare_to_upload(slip: Payslip, parsed_rows: list) -> dict:
    """Compare the computed payslip against an uploaded one, bucket by bucket.

    Comparing per bucket rather than per row is what makes this usable: an
    employer splits sick leave into three lines (sick pay, sick deduction,
    waiting-day deduction) where this app carries one net deduction, and a
    row-by-row diff would flag three mismatches on a month that is actually
    correct.

    `parsed_rows` are the Row objects from app/core/payslip_import.py. Returns
    per-bucket lines plus the totals, each with a signed `diff` (uploaded minus
    computed) and a `matched` flag.
    """
    computed = slip.bucket_totals()

    uploaded: dict[str, float] = {}
    unknown_rows = []
    for row in parsed_rows:
        category = getattr(row, "category", None) or "unknown"
        amount = float(getattr(row, "amount", 0.0) or 0.0)
        if category == "unknown":
            unknown_rows.append(row)
            continue
        # Tax-free expense reimbursements are not pay and never reach gross.
        if category == "expense":
            continue
        uploaded[COMPARE_BUCKETS.get(category, category)] = (
            uploaded.get(COMPARE_BUCKETS.get(category, category), 0.0) + amount
        )

    lines = []
    for bucket in sorted(set(computed) | set(uploaded), key=_bucket_sort_key):
        ours = computed.get(bucket, 0.0)
        theirs = uploaded.get(bucket, 0.0)
        diff = theirs - ours
        lines.append(
            {
                "bucket": bucket,
                "computed": ours,
                "uploaded": theirs,
                "diff": diff,
                "matched": abs(diff) <= MATCH_TOLERANCE,
                "missing_here": bucket not in computed,
                "missing_there": bucket not in uploaded,
            }
        )

    return {"lines": lines, "unknown_rows": unknown_rows}


def _bucket_sort_key(bucket: str) -> int:
    """Order buckets like the payslip rows, unknown ones last."""
    for index, key in enumerate(ROW_ORDER):
        if COMPARE_BUCKETS.get(key, key) == bucket:
            return index
    return len(ROW_ORDER)
