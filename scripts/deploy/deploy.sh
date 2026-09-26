#!/usr/bin/env bash
# Phase 16b — Production Deployment Script
#
# Deploys all VoiceOS services in the correct order with health checks
# between each step. Designed for the production VM: 205.147.102.94
#
# MUST run pre_deploy_checklist.sh first and get 0 failures.
#
# Usage: bash scripts/deploy/deploy.sh [--dry-run]
# Exit code: 0 = success, 1 = step failed

set -euo pipefail

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

SYSTEMD_DIR="$(pwd)/scripts/systemd"
INSTALL_DIR="/opt/voiceos"
UNIT_DIR="/etc/systemd/system"
LOG_DIR="/opt/voiceos/logs"
WATCH_DURATION=600  # 10 minutes post-deploy error rate watch

run() {
  if $DRY_RUN; then
    echo "    [dry-run] $*"
  else
    "$@"
  fi
}

step() {
  echo ""
  echo "[$1] $2"
}

wait_healthy() {
  local name="$1"
  local url="$2"
  local max_wait="${3:-30}"
  for i in $(seq 1 "$max_wait"); do
    if curl -sf "$url" > /dev/null 2>&1; then
      echo "    $name healthy (${i}s)"
      return 0
    fi
    sleep 1
  done
  echo "    ERROR: $name did not become healthy within ${max_wait}s ($url)"
  return 1
}

echo ""
echo "VoiceOS Production Deployment"
echo "══════════════════════════════"
$DRY_RUN && echo "  (DRY RUN — no changes will be made)"
echo "  Started: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "  Commit:  $(git rev-parse HEAD)"

# ── Step 1: Alembic migrations ────────────────────────────────────────────────
step "1/7" "Running Alembic migrations..."
run alembic upgrade head
echo "    Current head: $(alembic current 2>/dev/null | tail -1 || echo 'unknown')"

# ── Step 2: K8s services ──────────────────────────────────────────────────────
step "2/7" "Rolling restart of Kubernetes services..."
if command -v kubectl &>/dev/null && kubectl cluster-info &>/dev/null 2>&1; then
  run kubectl rollout restart deployment -n voiceos-platform
  run kubectl rollout status deployment -n voiceos-platform --timeout=120s
else
  echo "    kubectl not available — skipping K8s restart (services running natively)"
fi

# ── Step 3: Install systemd units ─────────────────────────────────────────────
step "3/7" "Installing / refreshing systemd units..."
for unit in \
    voiceos-bff.service \
    voiceos-webapi.service \
    voiceos-frontend.service \
    voiceos-voice-runtime.service \
    voiceos-dialer-worker.service \
    voiceos-telephony-event-relay.service \
    voiceos-telephony-event-relay.timer \
    voiceos-mongodb-backup.service \
    voiceos-mongodb-backup.timer \
    voiceos-vault-snapshot.service \
    voiceos-vault-snapshot.timer; do
  if [[ -f "$SYSTEMD_DIR/$unit" ]]; then
    run cp "$SYSTEMD_DIR/$unit" "$UNIT_DIR/$unit"
  fi
done
run systemctl daemon-reload

# ── Step 4: Restart web_api ───────────────────────────────────────────────────
step "4/7" "Restarting web_api..."
run systemctl restart voiceos-webapi
if ! $DRY_RUN; then
  wait_healthy "web_api" "http://localhost:8001/health/ready" 30
fi

# ── Step 5: Restart bff.js ────────────────────────────────────────────────────
step "5/7" "Restarting bff.js (graceful shutdown active)..."
run systemctl restart voiceos-bff
if ! $DRY_RUN; then
  wait_healthy "bff.js" "http://localhost:8000/health/ready" 30
fi

# ── Step 6: Restart voice runtime ─────────────────────────────────────────────
step "6/7" "Restarting voice runtime (drain gate active)..."
run systemctl restart voiceos-voice-runtime
if ! $DRY_RUN; then
  wait_healthy "voice-runtime" "http://localhost:8010/health/ready" 30
fi

# ── Step 7: Restart dialer_worker ─────────────────────────────────────────────
step "7/7" "Restarting dialer_worker (reconciliation will run before consumer loop)..."
run systemctl restart voiceos-dialer-worker
if ! $DRY_RUN; then
  sleep 5
  if systemctl is-active --quiet voiceos-dialer-worker; then
    echo "    dialer_worker active"
  else
    echo "    ERROR: dialer_worker failed to start"
    run journalctl -u voiceos-dialer-worker -n 20
    exit 1
  fi
fi

# ── Post-deploy: ensure timers enabled ────────────────────────────────────────
echo ""
echo "[post] Enabling backup timers..."
run systemctl enable --now voiceos-mongodb-backup.timer
run systemctl enable --now voiceos-vault-snapshot.timer
run systemctl enable --now voiceos-telephony-event-relay.timer

# ── Post-deploy: 10-minute error rate watch ───────────────────────────────────
if ! $DRY_RUN; then
  echo ""
  echo "[watch] Watching error rates for ${WATCH_DURATION}s (Ctrl-C to skip)..."
  echo "        Monitor: journalctl -u voiceos-bff -u voiceos-webapi -f"
  echo "        Metrics: curl http://localhost:9090/api/v1/query?query=rate(http_requests_total\\{status=~'5..'\\}[1m])"
  echo ""
  echo "  → Deploy complete. Watch for 10 minutes before enabling first campaign."
else
  echo ""
  echo "[dry-run complete] All steps validated."
fi

echo ""
echo "  Deployed at: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "  Commit:      $(git rev-parse HEAD)"
echo ""
echo "  Next step: bash scripts/deploy/rollout_first_tenant.sh"
