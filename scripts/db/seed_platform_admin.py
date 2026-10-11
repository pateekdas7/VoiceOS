"""Seed a PLATFORM_ADMIN user with password-based login.

Usage:
  python scripts/db/seed_platform_admin.py \
      --email admin@voiceos.local \
      --name "VoiceOS Admin" \
      --password <secret>

Requires DATABASE_URL or POSTGRES_DSN in the environment (or .env file).
Idempotent: updates the password_hash if the user already exists.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import bcrypt
import psycopg2

# Allow running from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
except ImportError:
    pass


def get_dsn() -> str:
    for key in ("DATABASE_URL", "POSTGRES_DSN"):
        val = os.getenv(key)
        if val:
            return val
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5432")
    db   = os.getenv("POSTGRES_DB", "voiceos")
    user = os.getenv("POSTGRES_USER", "voiceos")
    pw   = os.getenv("POSTGRES_PASSWORD", "")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a platform admin user")
    parser.add_argument("--email",    required=True)
    parser.add_argument("--name",     required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--role",     default="PLATFORM_ADMIN",
                        choices=["PLATFORM_ADMIN", "PLATFORM_SUPPORT", "PLATFORM_BILLING_OPS"])
    args = parser.parse_args()

    pw_hash = bcrypt.hashpw(args.password.encode(), bcrypt.gensalt()).decode()
    now = datetime.now(timezone.utc)

    dsn = get_dsn()
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO platform_users (email, name, platform_role, password_hash, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, TRUE, %s, %s)
        ON CONFLICT (email) DO UPDATE
          SET password_hash  = EXCLUDED.password_hash,
              name           = EXCLUDED.name,
              platform_role  = EXCLUDED.platform_role,
              is_active      = TRUE,
              updated_at     = EXCLUDED.updated_at
        RETURNING platform_user_id
        """,
        (args.email, args.name, args.role, pw_hash, now, now),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    print(f"OK — platform_user_id={row[0]}, email={args.email}, role={args.role}")


if __name__ == "__main__":
    main()
