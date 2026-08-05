#!/usr/bin/env python3
"""
Split existing sick_deduction overrides now that karens is its own payslip row.

Before this release the payslip carried one net sick deduction, karens included,
so an override on `sick_deduction` meant "the whole sick block is this amount".
The row now splits into `karens` (100% of the waiting-day hours) and
`sick_deduction` (20% of the rest), which leaves an old override sitting on the
smaller row with the karens row added on top: gross drops by the karens amount.

This rewrites each existing sick_deduction override into two, `karens` at the
amount the app computes for that month and `sick_deduction` at the remainder, so
the pair sums to exactly what the single override meant. Gross does not move.

Months whose payslip cannot be rebuilt (a person who left, a rotation that no
longer resolves) are reported and left alone rather than guessed at.

Idempotent: a month that already has a karens override is skipped.

Usage:
    python migrations/migrate_payslip_karens_split.py [--dry-run]
"""

import sys
from pathlib import Path

# Unlike the other migrations this one rebuilds a month through the app itself
# (the karens amount has to match build_payslip_rows exactly), so the repo root
# has to be importable when run as `python migrations/<this file>`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.database import PayslipOverride, SessionLocal  # noqa: E402


def _computed_karens(db, user_id: int, year: int, month: int) -> float | None:
    """The karens amount the app computes for this month, or None if unavailable.

    Read from the payslip rows rather than recomputed here, so this cannot drift
    from what build_payslip_rows produces.
    """
    from app.core.schedule.person_history import get_user_person_id
    from app.core.schedule.summary import summarize_month_for_person

    position = get_user_person_id(db, user_id)
    if position is None:
        return None

    summary = summarize_month_for_person(
        year=year, month=month, person_id=position, session=db, fetch_tax_table=False, wage_user_id=user_id
    )
    row = summary["payslip"].by_key().get("karens")
    # An overridden row carries the computed figure separately; a month with no
    # waiting-day hours has no karens row at all, and needs no split.
    if row is None:
        return None
    return row.computed_amount if row.overridden else row.amount


def migrate(dry_run: bool = False):
    """Run the migration."""
    print("Starting payslip karens split migration...")
    db = SessionLocal()
    try:
        existing = db.query(PayslipOverride).filter(PayslipOverride.row_key == "sick_deduction").all()
        if not existing:
            print("   No sick_deduction overrides, nothing to do")
            return

        have_karens = {
            (o.user_id, o.year, o.month)
            for o in db.query(PayslipOverride).filter(PayslipOverride.row_key == "karens").all()
        }

        split = skipped = 0
        for override in existing:
            key = (override.user_id, override.year, override.month)
            if key in have_karens:
                print(f"   {key} already split, skipping")
                skipped += 1
                continue

            karens = _computed_karens(db, *key)
            if karens is None or abs(karens) < 0.005:
                print(f"   {key} has no karens this month, leaving as is")
                skipped += 1
                continue

            total = override.amount
            remainder = round(total - karens, 2)
            print(f"   {key} {total} -> karens {round(karens, 2)} + sick_deduction {remainder}")

            if not dry_run:
                db.add(
                    PayslipOverride(
                        user_id=override.user_id,
                        year=override.year,
                        month=override.month,
                        row_key="karens",
                        amount=round(karens, 2),
                        reason=override.reason,
                        created_by=override.created_by,
                    )
                )
                override.amount = remainder
            split += 1

        if dry_run:
            print(f"Dry run: would split {split}, skip {skipped}")
            db.rollback()
        else:
            db.commit()
            print(f"Migration complete: split {split}, skipped {skipped}")
    finally:
        db.close()


if __name__ == "__main__":
    migrate(dry_run="--dry-run" in sys.argv)
