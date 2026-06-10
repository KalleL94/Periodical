#!/usr/bin/env python3
"""Migration: store API keys as SHA-256 hash plus Fernet-encrypted copy.

Adds the api_key_encrypted column and converts existing plaintext keys:
api_key gets the SHA-256 hex digest (used for authentication lookups) and
api_key_encrypted gets a Fernet token encrypted with a key derived from
SECRET_KEY (used to display the key on the profile page).

Requires the SECRET_KEY environment variable to be set to the same value the
application runs with, otherwise the encrypted copies cannot be decrypted.
Existing keys keep working unchanged for API clients.
"""

import base64
import hashlib
import os
import re
import sqlite3
import sys
from pathlib import Path

from cryptography.fernet import Fernet

# A SHA-256 hex digest; anything else in api_key is treated as a plaintext key
HASHED_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


def _fernet(secret_key: str) -> Fernet:
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def migrate(db_path: str = "app/database/schedule.db"):
    path = Path(db_path)
    if not path.exists():
        print(f"Error: Database not found at {path}")
        sys.exit(1)

    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    try:
        cursor.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in cursor.fetchall()]

        if "api_key_encrypted" not in columns:
            print("Adding column api_key_encrypted to users...")
            cursor.execute("ALTER TABLE users ADD COLUMN api_key_encrypted TEXT")
        else:
            print("Column api_key_encrypted already exists.")

        cursor.execute("SELECT id, api_key FROM users WHERE api_key IS NOT NULL")
        rows = cursor.fetchall()
        plaintext_rows = [(user_id, key) for user_id, key in rows if not HASHED_KEY_RE.match(key)]

        if plaintext_rows:
            # Strip whitespace/CR: python-dotenv strips line endings when the app
            # loads .env, so a shell-sourced SECRET_KEY with a trailing \r (CRLF
            # file) would otherwise derive a different encryption key.
            secret_key = (os.getenv("SECRET_KEY") or "").strip()
            if not secret_key:
                print("Error: SECRET_KEY must be set to encrypt existing API keys.")
                print("Run again with the same SECRET_KEY the application uses.")
                conn.rollback()
                sys.exit(1)

            fernet = _fernet(secret_key)
            for user_id, plaintext_key in plaintext_rows:
                hashed = hashlib.sha256(plaintext_key.encode("utf-8")).hexdigest()
                encrypted = fernet.encrypt(plaintext_key.encode("utf-8")).decode("utf-8")
                cursor.execute(
                    "UPDATE users SET api_key = ?, api_key_encrypted = ? WHERE id = ?",
                    (hashed, encrypted, user_id),
                )
            print(f"Converted {len(plaintext_rows)} plaintext API key(s) to hash + encrypted copy.")
        else:
            print("No plaintext API keys found, nothing to convert.")

        conn.commit()

    except sqlite3.Error as e:
        print(f"Error during migration: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Migration: Hash and encrypt stored API keys")
    print("=" * 60)
    db = sys.argv[1] if len(sys.argv) > 1 else "app/database/schedule.db"
    migrate(db)
    print("\nMigration completed successfully!")
