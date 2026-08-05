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
    "vacation_fixed",
    "vacation_variable",
    "vacation_variable_lump",
    "sick_pay",
    "sick_deduction",
    "karens",
    "vab_deduction",
    "leave_deduction",
)

# Rows whose amount is folded into gross outside summarize_month_for_person and
# added to the slip afterwards (the vacation supplement, via add_vacation_row).
# The general override adds its delta to summarize's gross, but the computed
# supplement is still folded in by each view separately, so an override would
# double count. These rows are read-only on the payslip: correct the vacation
# days instead, which is where the supplement is actually derived from.
NON_OVERRIDABLE_KEYS = frozenset({"vacation_fixed", "vacation_variable", "vacation_variable_lump"})

# Row keys the payslip groups differently from the way this app splits them.
# Both sides are summed per bucket before being compared, so a payslip that
# lists "Sjukavdrag", "Sjuklön" and "Karensavdrag" as three lines is not
# reported as three diffs against this app's single net sick deduction.
#
# OB3 and OB4 share wage code 152 on the payslip (see excel_shared.py
# REPORT_COL_HEADERS): "OB helg" and "OB helgdag" are one line there, so the
# app's two levels must be compared as one, or a correct month reports +X on
# one and -X on the other. This holds regardless of whether the two rates
# happen to be equal for a given user.
COMPARE_BUCKETS = {
    "sick_pay": "sick",
    "sick_deduction": "sick",
    "karens": "sick",
    "vacation_pay": "vacation",
    "vacation_fixed": "vacation",
    "vacation_variable": "vacation",
    "vacation_variable_lump": "vacation",
    "vacation_deduction": "vacation",
    "OB3": "ob_152",
    "OB4": "ob_152",
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

    # Sick pay here is the OB compensation paid on sick days; the wage part of
    # sick pay is already netted inside absence_deduction (see
    # calculate_absence_deduction: it returns karens + 20% of the sick hours).
    add("sick_pay", totals.get("sick_ob_pay", 0.0) or 0.0)

    # Split the single absence deduction per absence type, so each type gets the
    # payslip row it deserves rather than one opaque lump.
    per_type: dict[str, dict] = {}
    for detail in totals.get("absence_details") or []:
        entry = per_type.setdefault(detail["type"], {"deduction": 0.0, "hours": 0.0, "karens_hours": 0.0})
        entry["deduction"] += detail.get("deduction", 0.0) or 0.0
        entry["hours"] += detail.get("hours", 0.0) or 0.0
        entry["karens_hours"] += detail.get("karens_hours", 0.0) or 0.0

    # The same hourly wage calculate_absence_deduction priced the deduction with,
    # so the karens row split out below cannot drift from the figure it came from.
    hourly_wage = base_salary / _MONTHLY_HOURS if base_salary else 0.0

    for absence_type, key in (("SICK", "sick_deduction"), ("VAB", "vab_deduction"), ("LEAVE", "leave_deduction")):
        entry = per_type.get(absence_type)
        if not entry or not entry["deduction"]:
            continue
        deduction, hours = entry["deduction"], entry["hours"]
        # A payslip lists the waiting-day deduction as its own line. It is a full
        # 100% deduction inside this app's single net sick deduction, the rest
        # being 20% of the remaining sick hours, so splitting it out here leaves
        # the two rows summing to the same amount: gross does not move.
        karens_hours = entry["karens_hours"] if absence_type == "SICK" else 0.0
        if karens_hours:
            karens_amount = hourly_wage * karens_hours
            add("karens", -karens_amount, karens_hours, UNIT_HOURS)
            deduction -= karens_amount
            hours -= karens_hours
        add(key, -deduction, hours, UNIT_HOURS)

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


def apply_payslip_overrides(slip: Payslip, overrides: dict[str, dict]) -> dict[str, float]:
    """Replace computed rows with their manual overrides.

    Returns the per-row change (override minus computed) keyed by row key. The
    caller adds the sum to gross pay, and routes individual deltas back into the
    itemised totals the month and year views display (see
    _route_override_deltas), so an overridden row is consistent everywhere it
    appears, not only in the bottom-line gross.
    """
    if not overrides:
        return {}

    deltas: dict[str, float] = {}
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
        deltas[key] = amount - row.amount
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
    return deltas


# Payslip row key -> the totals field the month/year views display it through.
# Every row the views itemise is routed, so a hand-entered OB or on-call figure
# shows up in the month and year views rather than only in the gross total.
#
# OB, on-call and overtime also render in a per-day breakdown table that sums to
# the *computed* value, so a routed override leaves the aggregate above rows
# that no longer add up to it. The per-day rows are left alone (they are what
# the app computed, and rewriting them would invent hours nobody worked); the
# views mark the aggregate instead, from totals["override_deltas"].
#
# Rows with no totals field of their own (free-text rows, base salary, the
# vacation rows) move gross only, which is correct: nothing itemises them.
_OVERRIDE_TO_TOTAL = {
    # A deduction row is negative; its total is the positive amount deducted, so
    # a positive delta (less deducted) lowers the total by the same amount.
    "sick_deduction": ("absence_deduction", -1),
    "karens": ("absence_deduction", -1),
    "vab_deduction": ("absence_deduction", -1),
    "leave_deduction": ("absence_deduction", -1),
    # Sick-pay OB is added to gross, so its total moves with the delta directly.
    "sick_pay": ("sick_ob_pay", 1),
    "oc_vardag": ("oncall_pay", 1),
    "oc_helg": ("oncall_pay", 1),
    "oc_helgdag": ("oncall_pay", 1),
    "oc_storhelg": ("oncall_pay", 1),
    "ot": ("ot_pay", 1),
    "substitute": ("substitute_base_pay", 1),
}

# OB pay is itemised per OB code rather than as a single figure, so an OB
# override has to land inside totals["ob_pay"] under its own code.
_OB_KEYS = frozenset({"OB1", "OB2", "OB3", "OB4", "OB5"})


def route_override_deltas(totals: dict, deltas: dict[str, float]) -> None:
    """Apply per-row override deltas to the itemised totals the views display.

    Gross pay is handled by the caller; this keeps the itemised figures on the
    month and year views consistent with an overridden row, and records the
    deltas under totals["override_deltas"] so those views can mark which
    aggregates carry a manual adjustment.
    """
    if not deltas:
        return
    for key, (total_field, sign) in _OVERRIDE_TO_TOTAL.items():
        if key in deltas:
            totals[total_field] = totals.get(total_field, 0.0) + sign * deltas[key]

    ob_pay = totals.setdefault("ob_pay", {})
    for code in _OB_KEYS.intersection(deltas):
        ob_pay[code] = ob_pay.get(code, 0.0) + deltas[code]

    # Only rows that actually moved a figure. An override set to exactly what the
    # app computed is still a manual row, but marking the month's aggregate for it
    # would print "+0 kr" and say nothing.
    moved = {key: delta for key, delta in deltas.items() if abs(delta) >= 0.5}
    if moved:
        totals["override_deltas"] = moved


def add_vacation_rows(slip: Payslip, supplement: dict, days: int) -> None:
    """Add the vacation supplement rows: fixed part, variable part, variable lump.

    `supplement` is the dict from vacation.vacation_supplement_for_month. The
    supplement is folded into gross pay outside summarize_month_for_person (see
    fold_vacation_supplement_into_pay), so it is added to the payslip at the
    same point rather than inside the month summary, where it would be counted
    twice. Adding it here also means this must stay idempotent: the payslip
    route builds its context twice on an upload.

    The lump carries no quantity: it settles the whole year's variable part, not
    the days taken in the month it lands in.
    """
    rows = (
        ("vacation_fixed", supplement.get("fixed", 0.0), days or None),
        ("vacation_variable", supplement.get("variable", 0.0), days or None),
        ("vacation_variable_lump", supplement.get("lump", 0.0), None),
    )
    existing = {r.key for r in slip.rows}
    added = False
    for key, amount, qty in rows:
        if not amount or key in existing:
            continue
        slip.rows.append(PayslipRow(key=key, qty=qty, unit=UNIT_DAYS if qty else None, amount=amount))
        added = True

    if added:
        slip.rows.sort(key=lambda r: ROW_ORDER.index(r.key) if r.key in ROW_ORDER else len(ROW_ORDER))
        slip.total = sum(r.amount for r in slip.rows)


# Rounding slack when matching an uploaded payslip against the computed one.
# The employer rounds each line to ören, so exact equality would report noise.
MATCH_TOLERANCE = 1.0
# Hours/days are printed to one decimal, so anything under half of that is noise.
QTY_TOLERANCE = 0.05


def _bucket_aggregate(entries: list[tuple]) -> dict[str, dict]:
    """Group (bucket, qty, unit, amount) tuples into per-bucket figures.

    Amount is always summed. Quantity is only exposed when every contributing
    row has a quantity, shares one unit, and shares one sign: summing the day
    count of a vacation supplement and a vacation deduction, or hours of sick
    pay and a sick deduction, is meaningless, so those buckets compare on
    amount alone. Two positive OB rows (weekend + public holiday) do sum
    cleanly, so wage code 152 keeps its hours.
    """
    grouped: dict[str, list[tuple]] = {}
    for bucket, qty, unit, amount in entries:
        grouped.setdefault(bucket, []).append((qty, unit, amount))

    out: dict[str, dict] = {}
    for bucket, rows in grouped.items():
        amount = sum(a for _, _, a in rows)
        units = {u for _, u, _ in rows if u}
        have_all_qty = all(q is not None for q, _, _ in rows)
        signs = {(a > 0) for _, _, a in rows if abs(a) > MATCH_TOLERANCE}
        qty = unit = price = None
        if have_all_qty and len(units) == 1 and len(signs) <= 1:
            qty = sum(q for q, _, _ in rows)
            unit = next(iter(units))
            price = amount / qty if qty else None
        out[bucket] = {"amount": amount, "qty": qty, "unit": unit, "price": price}
    return out


def compare_to_upload(slip: Payslip, parsed_rows: list) -> dict:
    """Compare the computed payslip against an uploaded one, bucket by bucket.

    Comparing per bucket rather than per row is what makes this usable: an
    employer splits sick leave into three lines (sick pay, sick deduction,
    waiting-day deduction) where this app carries one net deduction, and a
    row-by-row diff would flag three mismatches on a month that is actually
    correct.

    Each line also carries the quantity, unit and unit price for both sides
    when the bucket has a comparable quantity, so an amount diff can be read as
    a rate difference (same hours, different a-price) rather than just a number.

    `parsed_rows` are the Row objects from app/core/payslip_import.py. Returns
    per-bucket lines plus the totals, each with a signed `diff` (uploaded minus
    computed) and a `matched` flag.
    """
    computed = _bucket_aggregate([(COMPARE_BUCKETS.get(r.key, r.key), r.qty, r.unit, r.amount) for r in slip.rows])

    uploaded_entries = []
    unknown_rows = []
    for row in parsed_rows:
        category = getattr(row, "category", None) or "unknown"
        if category == "unknown":
            unknown_rows.append(row)
            continue
        # Tax-free expense reimbursements are not pay and never reach gross.
        if category == "expense":
            continue
        uploaded_entries.append(
            (
                COMPARE_BUCKETS.get(category, category),
                getattr(row, "qty", None),
                getattr(row, "unit", None),
                float(getattr(row, "amount", 0.0) or 0.0),
            )
        )
    uploaded = _bucket_aggregate(uploaded_entries)

    lines = []
    for bucket in sorted(set(computed) | set(uploaded), key=_bucket_sort_key):
        ours = computed.get(bucket, {})
        theirs = uploaded.get(bucket, {})
        our_amount = ours.get("amount", 0.0)
        their_amount = theirs.get("amount", 0.0)
        diff = their_amount - our_amount

        amount_ok = abs(diff) <= MATCH_TOLERANCE
        # A quantity mismatch is only meaningful when both sides expose one.
        qty_ok = True
        if ours.get("qty") is not None and theirs.get("qty") is not None:
            qty_ok = abs(theirs["qty"] - ours["qty"]) <= QTY_TOLERANCE

        lines.append(
            {
                "bucket": bucket,
                "computed": our_amount,
                "uploaded": their_amount,
                "diff": diff,
                "computed_qty": ours.get("qty"),
                "uploaded_qty": theirs.get("qty"),
                "computed_price": ours.get("price"),
                "uploaded_price": theirs.get("price"),
                "unit": ours.get("unit") or theirs.get("unit"),
                "qty_mismatch": not qty_ok,
                "matched": amount_ok and qty_ok,
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
