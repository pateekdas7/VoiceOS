#!/usr/bin/env bash
# Phase 16c — Single-Tenant First Rollout
#
# Enables production dialing for one test tenant with 10 leads in simulation
# mode. Verifies end-to-end trace before expanding to other tenants.
#
# Usage: bash scripts/deploy/rollout_first_tenant.sh --tenant-id <uuid> [--dry-run]

set -euo pipefail

TENANT_ID=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tenant-id) TENANT_ID="$2"; shift 2 ;;
    --dry-run)   DRY_RUN=true; shift ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$TENANT_ID" ]]; then
  echo "Usage: $0 --tenant-id <uuid> [--dry-run]"
  exit 1
fi

BFF_URL="http://localhost:8000"
DB_DSN="${DATABASE_URL:-postgresql://localhost/voiceos}"

run() {
  $DRY_RUN && echo "  [dry-run] $*" || "$@"
}

psql_query() {
  $DRY_RUN && echo "  [dry-run SQL] $1" || psql "$DB_DSN" -t -c "$1"
}

echo ""
echo "Phase 16c — Single-Tenant Rollout"
echo "══════════════════════════════════"
echo "  Tenant: $TENANT_ID"
$DRY_RUN && echo "  (DRY RUN)"
echo ""

# ── 1. Verify tenant exists ───────────────────────────────────────────────────
echo "[1] Verifying tenant..."
if ! $DRY_RUN; then
  result=$(psql "$DB_DSN" -t -c "SELECT name, status FROM tenants WHERE tenant_id='$TENANT_ID'" 2>/dev/null)
  if [[ -z "$result" ]]; then
    echo "  ERROR: Tenant $TENANT_ID not found in database"
    exit 1
  fi
  echo "  $result"
fi

# ── 2. Create a test campaign with simulation mode ────────────────────────────
echo "[2] Creating simulation campaign (10 leads)..."
psql_query """
INSERT INTO campaigns (campaign_id, tenant_id, name, product, status, dialing_mode, created_at)
VALUES (gen_random_uuid(), '$TENANT_ID', 'Production Pilot', 'collections',
        'DRAFT', 'SIMULATION', NOW())
ON CONFLICT DO NOTHING;
"""

# ── 3. Verify bff.js and web_api healthy ──────────────────────────────────────
echo "[3] Health checks..."
for svc in "bff.js:http://localhost:8000/health/ready" "web_api:http://localhost:8001/health/ready"; do
  name="${svc%%:*}"
  url="${svc#*:}"
  if $DRY_RUN; then
    echo "  [dry-run] curl $url"
  elif curl -sf "$url" > /dev/null; then
    echo "  ✓ $name healthy"
  else
    echo "  ✗ $name not responding at $url"
    exit 1
  fi
done

# ── 4. Enable simulation dialing ──────────────────────────────────────────────
echo "[4] Activating simulation mode for tenant..."
psql_query """
UPDATE tenants
SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"dialing_enabled": true, "mode": "simulation"}'::jsonb
WHERE tenant_id = '$TENANT_ID';
"""

# ── 5. Verify dialer_worker is consuming ──────────────────────────────────────
echo "[5] Verifying dialer_worker active..."
if $DRY_RUN; then
  echo "  [dry-run] systemctl is-active voiceos-dialer-worker"
elif systemctl is-active --quiet voiceos-dialer-worker; then
  echo "  ✓ dialer_worker active"
else
  echo "  ✗ dialer_worker not running — check: journalctl -u voiceos-dialer-worker -n 30"
  exit 1
fi

# ── 6. Watch for first call trace ─────────────────────────────────────────────
echo ""
echo "[6] Instructions for end-to-end trace verification:"
echo "    1. Upload 10 leads to the simulation campaign in the UI"
echo "    2. Activate the campaign"
echo "    3. Watch: journalctl -u voiceos-dialer-worker -f | grep call_attempt"
echo "    4. Verify: SELECT * FROM call_attempts ORDER BY created_at DESC LIMIT 5;"
echo "    5. Verify: SELECT * FROM active_calls ORDER BY created_at DESC LIMIT 5;"
echo ""
echo "    If simulation completes clean → expand to real dialing:"
echo "    bash scripts/deploy/expand_rollout.sh --tenant-id $TENANT_ID"
echo ""
$DRY_RUN && echo "  [dry-run complete]" || echo "  Tenant pilot activated."
