#!/usr/bin/env python3
"""Report which accounts are sitting on a password that was once committed to git.

`must_change_password = 1` does not answer this on its own. Five places set that
flag and four of them use a password the admin typed in; only the reset button
and the seeder ever used a constant from the source. So this tests the stored
hashes directly against the values that used to be in the repository.

Read-only: opens the database with `mode=ro` and never writes to it.

--db is required on purpose. It defaulted to /opt/Periodical/... once, which is
a path that exists on more than one host: on a decommissioned server it silently
answered for a stale copy of the database instead of the one the app is serving.
A credential check that quietly tests the wrong database is worse than no check,
so the caller names the file and the report repeats back the host, the row count
and the last write for it to be checked against.

Usage:
    venv/bin/python3 scripts/check_default_passwords.py --db app/database/schedule.db

Exit codes: 0 nothing exposed, 1 at least one account exposed, 2 could not run.
"""

import argparse
import socket
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
    parser.add_argument("--db", required=True, help="Database to check. Required: see the module docstring")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"Write the report here (default: {DEFAULT_OUT})")
    args = parser.parse_args()

    db_path = Path(args.db)
    header = [
        "Periodical - committed-password exposure check",
        f"Run:       {datetime.now().isoformat(timespec='seconds')}",
        f"Host:      {socket.gethostname()}",
        f"Database:  {db_path.resolve()}",
    ]
    if db_path.exists():
        modified = datetime.fromtimestamp(db_path.stat().st_mtime).isoformat(timespec="seconds")
        header.append(f"Last write: {modified}   <- confirm this is the live database, not a stale copy")
    header.append("")

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
