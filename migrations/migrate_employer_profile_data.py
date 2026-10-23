#!/usr/bin/env python3
"""One-off data rewrite for the employer-profile release.

Three steps, each skipped when already done, so the script is safe to run twice:

1. Pierre's day_pay_overrides row for 2026-08-22: rename the on-call key
   OC_WEEKEND_SAT to NX_WEEKEND (the Nexer weekend code).
2. Drop the `oncall` key from users.custom_rates and every rate_history.rates
   row for Al-Amin, Pierre, Adriana and Rickard. Their on-call pricing now comes
   from the employer profile. Every other key is left alone.
3. Pierre's manual overtime rows 2026-08-30 (2.0 h), 2026-08-31 (8.5 h) and
   2026-09-01 (6.5 h), all 22:00-06:30: set the first to 8.5 h and delete the
   third. Only done when exactly that pattern is present. The stored ot_pay is
   write-only (nothing reads it back for pricing), so it is left as is.

Profiles themselves are set by an admin in the UI after deploy.

Usage:
    python migrations/migrate_employer_profile_data.py [--dry-run]
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

NAMES = ("Pierre", "Al-Amin", "Adriana", "Rickard")
OVERRIDE_USER = "Pierre"
OVERRIDE_DATE = "2026-08-22"
OLD_KEY, NEW_KEY = "OC_WEEKEND_SAT", "NX_WEEKEND"
OT_ROWS = {"2026-08-30": 2.0, "2026-08-31": 8.5, "2026-09-01": 6.5}


def _load(value):
    return json.loads(value) if isinstance(value, str) else value


def migrate(engine, dry_run: bool = False) -> dict[str, int]:
    """Apply all three steps. Returns how many rows each one changed (or would change)."""
    result = {"override_renamed": 0, "custom_rates_cleaned": 0, "rate_history_cleaned": 0, "overtime_fixed": 0}
    marks = ",".join(f":n{i}" for i in range(len(NAMES)))
    names = {f"n{i}": n for i, n in enumerate(NAMES)}

    with engine.begin() as c:
        rows = c.execute(
            text(
                "SELECT o.id, o.oncall_hours_override FROM day_pay_overrides o"
                " JOIN users u ON u.id = o.user_id WHERE u.name = :name AND o.date = :d"
            ),
            {"name": OVERRIDE_USER, "d": OVERRIDE_DATE},
        ).all()
        for row_id, raw in rows:
            hours = _load(raw)
            if not isinstance(hours, dict) or OLD_KEY not in hours or NEW_KEY in hours:
                continue
            renamed = {(NEW_KEY if k == OLD_KEY else k): v for k, v in hours.items()}
            result["override_renamed"] += 1
            if not dry_run:
                c.execute(
                    text("UPDATE day_pay_overrides SET oncall_hours_override = :v WHERE id = :id"),
                    {"v": json.dumps(renamed), "id": row_id},
                )

        for key, sql, table, col in (
            (
                "custom_rates_cleaned",
                f"SELECT id, custom_rates FROM users WHERE name IN ({marks})",
                "users",
                "custom_rates",
            ),
            (
                "rate_history_cleaned",
                f"SELECT h.id, h.rates FROM rate_history h JOIN users u ON u.id = h.user_id WHERE u.name IN ({marks})",
                "rate_history",
                "rates",
            ),
        ):
            for row_id, raw in c.execute(text(sql), names).all():
                rates = _load(raw)
                if not isinstance(rates, dict) or "oncall" not in rates:
                    continue
                rates = {k: v for k, v in rates.items() if k != "oncall"}
                result[key] += 1
                if not dry_run:
                    c.execute(
                        text(f"UPDATE {table} SET {col} = :v WHERE id = :id"),
                        {"v": json.dumps(rates), "id": row_id},
                    )

        ot = c.execute(
            text(
                "SELECT o.id, substr(o.date, 1, 10), o.hours FROM overtime_shifts o"
                " JOIN users u ON u.id = o.user_id WHERE u.name = :name AND o.kind = 'ot' AND o.side = 'full'"
                " AND substr(o.start_time, 1, 5) = '22:00' AND substr(o.end_time, 1, 5) = '06:30'"
                " AND substr(o.date, 1, 10) IN ('2026-08-30', '2026-08-31', '2026-09-01')"
            ),
            {"name": OVERRIDE_USER},
        ).all()
        if {d: h for _, d, h in ot} == OT_ROWS and len(ot) == len(OT_ROWS):
            ids = {d: i for i, d, _ in ot}
            result["overtime_fixed"] = 1
            if not dry_run:
                c.execute(text("UPDATE overtime_shifts SET hours = 8.5 WHERE id = :id"), {"id": ids["2026-08-30"]})
                c.execute(text("DELETE FROM overtime_shifts WHERE id = :id"), {"id": ids["2026-09-01"]})
    return result


def main() -> int:
    from app.database.database import DATABASE_URL, engine

    dry_run = "--dry-run" in sys.argv[1:]
    print(f"{'Dry run on' if dry_run else 'Migrating'} {DATABASE_URL}")
    result = migrate(engine, dry_run=dry_run)
    verb = "would change" if dry_run else "changed"
    print(
        f"  override rows renamed ({verb}): {result['override_renamed']}\n"
        f"  users.custom_rates cleaned ({verb}): {result['custom_rates_cleaned']}\n"
        f"  rate_history rows cleaned ({verb}): {result['rate_history_cleaned']}\n"
        f"  overtime rows corrected ({verb}): {result['overtime_fixed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
