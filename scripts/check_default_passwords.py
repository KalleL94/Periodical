#!/usr/bin/env python3
"""Report which accounts are sitting on a password that was once committed to git.

`must_change_password = 1` does not answer this on its own. Five places set that
flag and four of them use a password the admin typed in; only the reset button
and the seeder ever used a constant from the source. So this tests the stored
hashes directly against the values that used to be in the repository.

Read-only: opens the database with `mode=ro` and never writes to it.

Usage:
    venv/bin/python3 scripts/check_default_passwords.py
    venv/bin/python3 scripts/check_default_passwords.py --db path/to/other.db

Exit codes: 0 nothing exposed, 1 at least one account exposed, 2 could not run.
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

try:
    import bcrypt
except ImportError:
    print("Error: bcrypt is not installed. Run this with the project venv:", file=sys.stderr)
    print("  venv/bin/python3 scripts/check_default_passwords.py", file=sys.stderr)
    sys.exit(2)

DEFAULT_DB = "/opt/Periodical/app/database/schedule.db"
DEFAULT_OUT = "/tmp/periodical-default-password-check.txt"

# Passwords that were committed to the public repository before they were
# replaced by secrets.token_urlsafe(12). Anyone who read the source knew them.
KNOWN_PASSWORDS = {
    "London1": "old admin-reset default (app/core/constants.py)",
    "Banan1": "old seeded admin password (migrations/migrate_to_db.py)",
}


def check(db_path: str) -> tuple[int, list[str]]:
    """Return (accounts checked, report lines)."""
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT id, username, role, is_active, must_change_password, password_hash FROM users ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    lines = []
    for user_id, username, role, is_active, must_change, password_hash in rows:
        if not password_hash or not password_hash.startswith("$2"):
            # Not a bcrypt hash. Worth saying out loud rather than skipping in
            # silence: the app cannot verify it either, so nobody can log in.
            lines.append(f"  UNREADABLE  id={user_id:<4} {username:<12} hash is not bcrypt")
            continue
        for candidate, origin in KNOWN_PASSWORDS.items():
            if bcrypt.checkpw(candidate.encode("utf-8"), password_hash.encode("utf-8")):
                lines.append(
                    f"  EXPOSED     id={user_id:<4} {username:<12} role={role:<5} "
                    f"active={is_active} must_change={must_change}  -> {candidate}  [{origin}]"
                )

    return len(rows), lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DEFAULT_DB, help=f"Database to check (default: {DEFAULT_DB})")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"Write the report here (default: {DEFAULT_OUT})")
    args = parser.parse_args()

    header = [
        "Periodical - committed-password exposure check",
        f"Run:      {datetime.now().isoformat(timespec='seconds')}",
        f"Database: {args.db}",
        "",
    ]

    try:
        total, findings = check(args.db)
    except FileNotFoundError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except sqlite3.Error as error:
        print(f"Error reading the database: {error}", file=sys.stderr)
        return 2

    body = [f"Checked {total} accounts.", ""]
    exposed = [line for line in findings if "EXPOSED" in line]
    if exposed:
        body.append(f"{len(exposed)} account(s) are on a password that was published in the repo.")
        body.append("Rotate them from /admin/users once this branch is deployed, which")
        body.append("generates a random password instead of the old constant.")
        body.append("")
    else:
        body.append("No account is on a formerly-committed password. Nothing to rotate.")
        body.append("")
    body.extend(findings)

    report = "\n".join(header + body) + "\n"
    Path(args.out).write_text(report, encoding="utf-8")
    print(report)
    print(f"Report written to {args.out}")
    return 1 if exposed else 0


if __name__ == "__main__":
    sys.exit(main())
