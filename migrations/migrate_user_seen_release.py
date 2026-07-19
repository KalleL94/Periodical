#!/usr/bin/env python3
"""Migration: remember which changelog release each user has read.

Adds one nullable column to the ``users`` table:
  - ``seen_release``: the last changelog version the user acknowledged by opening
    the changelog page. NULL means never opened, which the app treats as unread.

Leaving existing rows at NULL is deliberate: every user is shown the release
notes once after this ships, which is the point of the feature.

The script is idempotent: the column is added only if it is not already present
(checked via ``PRAGMA table_info``). The DB path may be passed as the first argument
and defaults to the local development database.

Production note: back up the production database BEFORE running this migration, e.g.
    sqlite3 /opt/Periodical/app/database/schedule.db \
        ".backup /opt/Periodical/app/database/schedule.db.bak"
then run:
    python migrations/migrate_user_seen_release.py /opt/Periodical/app/database/schedule.db
"""

import sqlite3
import sys

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "app/database/schedule.db"

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

existing = [row[1] for row in cur.execute("PRAGMA table_info(users)").fetchall()]

if "seen_release" not in existing:
    cur.execute("ALTER TABLE users ADD COLUMN seen_release VARCHAR(20)")
    conn.commit()
    print("Done: added column users.seen_release.")
else:
    print("Already exists: seen_release column.")

conn.close()
