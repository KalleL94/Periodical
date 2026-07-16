#!/usr/bin/env python3
"""Migration: link substitutes to user accounts and give them an hourly wage.

Adds two nullable columns to the ``substitutes`` table:
  - ``user_id``: FK to ``users.id`` (nullable). Links a substitute to a User account
    so their pre-employment shifts render and price in that user's personal views.
  - ``hourly_wage``: hourly wage in SEK (nullable), same semantics as ``users.wage``
    when the wage type is HOURLY.

The script is idempotent: each column is added only if it is not already present
(checked via ``PRAGMA table_info``). The DB path may be passed as the first argument
and defaults to the local development database.

Production note: back up the production database BEFORE running this migration, e.g.
    sqlite3 /opt/Periodical/app/database/schedule.db \
        ".backup /opt/Periodical/app/database/schedule.db.bak"
then run:
    python migrations/migrate_substitute_account_link.py /opt/Periodical/app/database/schedule.db
"""

import sqlite3
import sys

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "app/database/schedule.db"

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

existing = [row[1] for row in cur.execute("PRAGMA table_info(substitutes)").fetchall()]

added = []
if "user_id" not in existing:
    cur.execute("ALTER TABLE substitutes ADD COLUMN user_id INTEGER REFERENCES users(id)")
    added.append("user_id")
if "hourly_wage" not in existing:
    cur.execute("ALTER TABLE substitutes ADD COLUMN hourly_wage INTEGER")
    added.append("hourly_wage")

if added:
    conn.commit()
    print(f"Done: added columns to substitutes: {', '.join(added)}.")
else:
    print("Already exists: user_id and hourly_wage columns.")

conn.close()
