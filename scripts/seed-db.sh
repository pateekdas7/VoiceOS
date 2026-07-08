#!/usr/bin/env bash
# scripts/seed-db.sh — Seed Postgres with test tenant and customer fixture data.
#
# Creates:
#   - 1 test tenant  (tenant_id: 00000000-0000-0000-0000-000000000001)
#   - 3 test customers linked to that tenant
#
# Requires POSTGRES_DSN or runs against the default docker-compose Postgres.
#
# Usage:
#   bash scripts/seed-db.sh
#   POSTGRES_DSN=postgresql://user:pass@host/db bash scripts/seed-db.sh
#
# Architecture: V5 Ch2 (Multi-Tenancy); V5 Ch4 (CRM); DocSuite-09.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

log() { echo "[seed-db] $*"; }
err() { echo "[seed-db] ERROR: $*" >&2; exit 1; }

cd "$PROJECT_ROOT"

POSTGRES_DSN="${POSTGRES_DSN:-postgresql://voiceos:voiceos_dev_pw@localhost:5432/voiceos_dev}"

# Verify psql is available
if ! command -v psql >/dev/null 2>&1; then
    err "psql not found. Install postgresql-client or run inside the postgres container."
fi

log "Connecting to: $POSTGRES_DSN"
log "Applying Sprint-002 migrations (idempotent)..."

for f in "$PROJECT_ROOT"/scripts/db/migrations/*.sql; do
    log "  Running $(basename "$f")..."
    psql "$POSTGRES_DSN" -f "$f" -q
done

log "Seeding test tenant..."
psql "$POSTGRES_DSN" -q <<'SQL'
-- Test tenant (idempotent upsert)
INSERT INTO tenants (
    tenant_id, name, slug, status, isolation_profile,
    plan_tier, created_at, updated_at
) VALUES (
    '00000000-0000-0000-0000-000000000001',
    'VoiceOS Test Tenant',
    'voiceos-test',
    'ACTIVE',
    'ROW_LEVEL',
    'SANDBOX',
    NOW(),
    NOW()
) ON CONFLICT (tenant_id) DO NOTHING;
SQL

log "Seeding 3 test customers..."
psql "$POSTGRES_DSN" -q <<'SQL'
-- Customer 1: borrower with active loan
INSERT INTO customers (
    customer_id, tenant_id, crm_id, name, preferred_language,
    is_active, data_erasure_requested, created_at, updated_at
) VALUES (
    '00000000-0000-0000-0001-000000000001',
    '00000000-0000-0000-0000-000000000001',
    'CRM-TEST-001',
    'Arjun Sharma',
    'hi',
    TRUE, FALSE, NOW(), NOW()
) ON CONFLICT (tenant_id, crm_id) DO NOTHING;

-- Customer 2: borrower with overdue payments
INSERT INTO customers (
    customer_id, tenant_id, crm_id, name, preferred_language,
    is_active, data_erasure_requested, created_at, updated_at
) VALUES (
    '00000000-0000-0000-0001-000000000002',
    '00000000-0000-0000-0000-000000000001',
    'CRM-TEST-002',
    'Priya Verma',
    'en',
    TRUE, FALSE, NOW(), NOW()
) ON CONFLICT (tenant_id, crm_id) DO NOTHING;

-- Customer 3: borrower who requested callback
INSERT INTO customers (
    customer_id, tenant_id, crm_id, name, preferred_language,
    is_active, data_erasure_requested, created_at, updated_at
) VALUES (
    '00000000-0000-0000-0001-000000000003',
    '00000000-0000-0000-0000-000000000001',
    'CRM-TEST-003',
    'Rahul Mehta',
    'hi',
    TRUE, FALSE, NOW(), NOW()
) ON CONFLICT (tenant_id, crm_id) DO NOTHING;
SQL

log ""
log "=== Seed complete ==="
log "Tenant  : 00000000-0000-0000-0000-000000000001 (VoiceOS Test Tenant)"
log "Customer: 00000000-0000-0000-0001-000000000001 (Arjun Sharma)"
log "Customer: 00000000-0000-0000-0001-000000000002 (Priya Verma)"
log "Customer: 00000000-0000-0000-0001-000000000003 (Rahul Mehta)"
