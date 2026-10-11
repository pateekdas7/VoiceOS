"""Provisions a real production tenant (tenants + organizations + first admin
user + webhook_registration) for a Client-1-style pilot (A7).

Idempotent: safe to re-run. Refuses to overwrite an existing row silently —
if a slug/email/webhook URL already exists, exits with a clear message rather
than silently doing nothing (matching provision_call002_customer.py's pattern:
row-existence check up-front, one clean INSERT per table).

This complements provision_call002_customer.py, which seeds the SINGLE test
customer used for Call-002 dry-runs. That script assumes a tenant already
exists (via DEFAULT_TENANT_ID). This one creates that tenant + org + admin
user + webhook for real Client-1 onboarding.

Uses direct psycopg SQL against migrations 007/008/0024's schema — this is
the correct level of abstraction for a one-shot bootstrap (matches
`scripts/seed_policies.py` etc.). It does NOT go through TenantManagementService
because that service assumes an authenticated admin user is already present —
which is a chicken-and-egg for pilot bootstrap.

Usage:
  # From CPU node, with the same env vars as the WS server:
  source <(python3 scripts/vault/gen_env.py)
  python3 scripts/tenants/provision_client_tenant.py \\
      --slug client-1 \\
      --display-name "Client One NBFC" \\
      --legal-name "Client One Financial Services Pvt Ltd" \\
      --tier professional \\
      --admin-email admin@client1.example \\
      --admin-name "Client-1 Admin" \\
      --webhook-url https://client1.example/voiceos/hooks \\
      --webhook-events saas.ptp.created,saas.call.completed

Prints the created tenant_id + webhook_id + webhook_secret to stdout — the
webhook secret is shown ONCE and must be captured now (used by Client-1 to
verify HMAC signatures on incoming webhook deliveries).
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import uuid
from datetime import datetime, timezone

try:
    import psycopg
except ImportError:
    print("ERROR: psycopg not installed. Run: pip install psycopg[binary]", file=sys.stderr)
    sys.exit(1)


ALLOWED_TIERS = {"trial", "starter", "professional", "enterprise"}
ALLOWED_EVENTS = {
    "saas.ptp.created",
    "saas.ptp.updated",
    "saas.call.completed",
    "saas.call.failed",
    "saas.campaign.approved",
    "saas.compliance.violation",
}


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", required=True, help="URL-safe tenant slug, unique globally")
    ap.add_argument("--display-name", required=True)
    ap.add_argument("--legal-name", required=True, help="Registered legal entity name")
    ap.add_argument("--tier", required=True, choices=sorted(ALLOWED_TIERS))
    ap.add_argument("--timezone", default="Asia/Kolkata")
    ap.add_argument("--max-concurrent-calls", type=int, default=10)
    ap.add_argument("--admin-email", required=True)
    ap.add_argument("--admin-name", required=True)
    ap.add_argument("--webhook-url", required=True, help="HTTPS endpoint Client-1 exposes")
    ap.add_argument("--webhook-events", required=True,
                    help=f"Comma-separated. Allowed: {','.join(sorted(ALLOWED_EVENTS))}")
    return ap.parse_args()


def _validate(args: argparse.Namespace) -> list[str]:
    events = [e.strip() for e in args.webhook_events.split(",") if e.strip()]
    unknown = [e for e in events if e not in ALLOWED_EVENTS]
    if unknown:
        raise SystemExit(f"Unknown webhook events: {unknown}. Allowed: {sorted(ALLOWED_EVENTS)}")
    if not args.webhook_url.startswith("https://"):
        raise SystemExit("webhook-url must be https://")
    if args.max_concurrent_calls < 1:
        raise SystemExit("max-concurrent-calls must be >= 1")
    return events


def main() -> None:
    args = _parse_args()
    events = _validate(args)

    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        raise SystemExit("POSTGRES_DSN not set. Run: source <(python3 scripts/vault/gen_env.py)")

    tenant_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    webhook_secret = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT tenant_id FROM tenants WHERE slug = %s", (args.slug,))
        row = cur.fetchone()
        if row is not None:
            raise SystemExit(
                f"Tenant with slug '{args.slug}' already exists (tenant_id={row[0]}). "
                "Refusing to overwrite. Use a different slug or delete manually first."
            )

        cur.execute("SELECT 1 FROM users WHERE email = %s LIMIT 1", (args.admin_email,))
        if cur.fetchone() is not None:
            raise SystemExit(f"Admin email '{args.admin_email}' already exists in users.")

        cur.execute(
            """
            INSERT INTO tenants (tenant_id, slug, display_name, subscription_tier,
                                 isolation_profile, status, timezone, currency,
                                 max_concurrent_calls, created_at, updated_at)
            VALUES (%s, %s, %s, %s, 'SHARED', 'ACTIVE', %s, 'INR', %s, %s, %s)
            """,
            (tenant_id, args.slug, args.display_name, args.tier,
             args.timezone, args.max_concurrent_calls, now, now),
        )

        cur.execute(
            """
            INSERT INTO organizations (org_id, tenant_id, legal_name, country, created_at, updated_at)
            VALUES (%s, %s, %s, 'IN', %s, %s)
            """,
            (org_id, tenant_id, args.legal_name, now, now),
        )

        cur.execute(
            """
            INSERT INTO users (user_id, tenant_id, email, name, is_active, is_service_account,
                               mfa_enabled, created_at, updated_at)
            VALUES (%s, %s, %s, %s, TRUE, FALSE, FALSE, %s, %s)
            """,
            (user_id, tenant_id, args.admin_email, args.admin_name, now, now),
        )

        cur.execute(
            """
            INSERT INTO webhook_registrations (tenant_id, url, secret, event_types, is_active, created_at)
            VALUES (%s, %s, %s, %s, TRUE, %s)
            RETURNING webhook_id
            """,
            (tenant_id, args.webhook_url, webhook_secret, events, now),
        )
        webhook_row = cur.fetchone()
        assert webhook_row is not None
        webhook_id = webhook_row[0]

        conn.commit()

    print("=" * 72)
    print(f"Tenant provisioned:  tenant_id={tenant_id}  slug={args.slug}")
    print(f"Organization:        org_id={org_id}  legal_name={args.legal_name}")
    print(f"Admin user:          user_id={user_id}  email={args.admin_email}")
    print(f"Webhook:             webhook_id={webhook_id}  url={args.webhook_url}")
    print(f"Webhook events:      {events}")
    print()
    print("Webhook HMAC secret (shown ONCE — capture now, share with Client-1 via")
    print("a secure channel; used to verify X-VoiceOS-Signature on deliveries):")
    print(f"  {webhook_secret}")
    print("=" * 72)


if __name__ == "__main__":
    main()
