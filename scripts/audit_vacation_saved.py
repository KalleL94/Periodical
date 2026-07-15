"""Read-only audit: list users whose closed vacation years may have paid out
previously saved days (bug fixed on branch fix/vacation-year-close).

A close is suspect when a year was closed with paid_out > 0 while saved days
from an earlier year existed. The script only prints; it never writes. Run it
against a BACKUP COPY of the production database and review the output with
the domain owner before any manual correction.

This heuristic is only meaningful for data written before the fix on this
branch: after the fix, paid_out > 0 with earlier saved days present is the
correct state, so this script is a one-time pre-fix sweep, not a recurring
monitor.

Usage: python3 scripts/audit_vacation_saved.py [path/to/schedule.db]
"""

import json
import sqlite3
import sys


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else "app/database/schedule.db"
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = conn.execute("SELECT id, name, vacation_saved FROM users WHERE vacation_saved IS NOT NULL").fetchall()

    suspects = 0
    for uid, name, raw in rows:
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            print(f"user {uid} ({name}): unparseable vacation_saved: {raw!r}")
            continue
        years = sorted((y for y in data if str(y).isdigit()), key=int)
        for y in years:
            entry = data.get(y) or {}
            if entry.get("paid_out", 0) > 0:
                earlier = {
                    e: (data.get(e) or {}).get("saved", 0)
                    for e in years
                    if int(e) < int(y) and (data.get(e) or {}).get("saved", 0) > 0
                }
                if earlier:
                    suspects += 1
                    print(
                        f"user {uid} ({name}): year {y} closed with paid_out="
                        f"{entry['paid_out']} ({entry.get('payout_amount', 0)} kr) "
                        f"while earlier saved days existed: {earlier}"
                    )
    conn.close()
    print(f"\n{suspects} suspect close(s) found across {len(rows)} user(s) with saved data.")


if __name__ == "__main__":
    main()
