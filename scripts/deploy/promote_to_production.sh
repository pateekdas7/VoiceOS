#!/usr/bin/env bash
# promote_to_production.sh — promote a verified staging build to production
#
# Usage:
#   ./promote_to_production.sh --tag <git-tag> [--skip-smoke] [--dry-run]
#
# Prerequisites:
#   - Staging is healthy (checked via /healthz)
#   - Tag matches staging deployment
#   - Running on the VoiceOS server

set -euo pipefail

APP_DIR=/opt/voiceos/app
ENV_FILE=/opt/voiceos/.env
LOG=/opt/voiceos/logs/promote.log
STAGING_BFF=http://127.0.0.1:8100
STAGING_API=http://127.0.0.1:8101
PROD_BFF=http://127.0.0.1:8000
PROD_API=http://127.0.0.1:8001
AUDIT_SCRIPT="$APP_DIR/scripts/deploy/release_audit.py"

DRY_RUN=false
SKIP_SMOKE=false
TAG=""

log()  { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"; }
die()  { log "ERROR: $*"; exit 1; }
run()  { log "RUN: $*"; $DRY_RUN && { log "(dry-run — skipped)"; return 0; }; eval "$*"; }

usage() {
  echo "Usage: $0 --tag <git-tag> [--skip-smoke] [--dry-run]"
  exit 1
}

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --tag)        TAG=$2;          shift 2;;
    --skip-smoke) SKIP_SMOKE=true; shift;;
    --dry-run)    DRY_RUN=true;    shift;;
    -h|--help)    usage;;
    *)            die "Unknown arg: $1";;
  esac
done
[[ -n "$TAG" ]] || usage

log "===== Promotion started: tag=$TAG dry_run=$DRY_RUN ====="

# ── Step 1: Verify staging health ────────────────────────────────────────────
log "Step 1: Checking staging health…"

check_health() {
  local url=$1 label=$2
  local code
  code=$(curl -sf -o /dev/null -w '%{http_code}' "${url}/healthz" 2>/dev/null || echo 000)
  if [[ "$code" != "200" ]]; then
    die "Staging $label unhealthy (HTTP $code) — promotion aborted"
  fi
  log "  $label OK (HTTP $code)"
}
check_health "$STAGING_BFF" "BFF"
check_health "$STAGING_API" "webapi"

# ── Step 2: Confirm tag on staging ───────────────────────────────────────────
log "Step 2: Verifying staging is running tag=$TAG…"
STAGING_VERSION=$(curl -sf "${STAGING_BFF}/version" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('version','unknown'))" 2>/dev/null || echo "unknown")
if [[ "$STAGING_VERSION" != "$TAG" && "$STAGING_VERSION" != "unknown" ]]; then
  die "Staging reports version=$STAGING_VERSION but expected tag=$TAG — deployment mismatch"
fi
log "  Staging version: $STAGING_VERSION"

# ── Step 3: Run smoke tests on staging ───────────────────────────────────────
if [[ "$SKIP_SMOKE" == false ]]; then
  log "Step 3: Running smoke tests against staging…"
  run "cd '$APP_DIR' && python -m pytest tests/smoke/ -q --timeout=30 \
    -x --base-url=$STAGING_BFF --api-url=$STAGING_API 2>&1 | tail -20"
else
  log "Step 3: Smoke tests skipped (--skip-smoke)"
fi

# ── Step 4: Record deployment start ──────────────────────────────────────────
log "Step 4: Recording deployment in audit trail…"
DEPLOY_ID=""
if [[ -f "$AUDIT_SCRIPT" && "$DRY_RUN" == false ]]; then
  DEPLOY_ID=$(/opt/voiceos/venv/bin/python3 "$AUDIT_SCRIPT" record \
    --env production \
    --tag "$TAG" \
    --deployed-by "${DEPLOY_USER:-promote-script}" \
    --notes "Promoted from staging via promote_to_production.sh" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null || echo "")
  log "  Deploy ID: $DEPLOY_ID"
fi

# ── Step 5: Run database migrations ─────────────────────────────────────────
log "Step 5: Running Alembic migrations…"
run "cd '$APP_DIR' && echo 'mamata@1976' | sudo -S -u voiceos \
  bash -c 'source /opt/voiceos/.env && alembic upgrade head'"

# ── Step 6: Restart production services (rolling) ────────────────────────────
log "Step 6: Restarting production services…"

restart_svc() {
  local svc=$1
  log "  Restarting $svc…"
  run "echo 'mamata@1976' | sudo -S systemctl restart '$svc'"
  sleep 3
  local active
  active=$(systemctl is-active "$svc" 2>/dev/null || echo inactive)
  [[ "$active" == "active" ]] || die "$svc failed to start (status=$active)"
  log "  $svc active"
}

restart_svc voiceos-webapi
restart_svc voiceos-bff
restart_svc "voiceos-dialer-worker@1"

# ── Step 7: Verify production health ─────────────────────────────────────────
log "Step 7: Verifying production health post-deploy…"
sleep 5
check_health "$PROD_BFF" "production BFF"
check_health "$PROD_API" "production webapi"

# ── Step 8: Mark deployment success ──────────────────────────────────────────
if [[ -n "$DEPLOY_ID" && "$DRY_RUN" == false ]]; then
  /opt/voiceos/venv/bin/python3 "$AUDIT_SCRIPT" finish --id "$DEPLOY_ID" --status success
  log "  Audit record $DEPLOY_ID marked success"
fi

log "===== Promotion COMPLETE: tag=$TAG is live in production ====="
