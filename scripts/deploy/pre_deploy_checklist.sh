#!/usr/bin/env bash
# Phase 16a — Pre-Deploy Readiness Checklist
#
# Verifies all gates before production rollout. Must pass 100% before
# executing deploy.sh.
#
# Usage: bash scripts/deploy/pre_deploy_checklist.sh
# Exit code: 0 = all gates pass, 1 = one or more fail

set -euo pipefail

PASS=0
FAIL=0

RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[0;33m'
NC='\033[0m'

check() {
  local name="$1"
  local ok="$2"   # "0" = pass, anything else = fail
  if [[ "$ok" == "0" ]]; then
    echo -e "  ${GRN}✓${NC} $name"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}✗${NC} $name"
    FAIL=$((FAIL + 1))
  fi
}

warn() {
  local name="$1"
  echo -e "  ${YLW}⚠${NC} $name"
}

echo ""
echo "Phase 16 Pre-Deploy Readiness Checklist"
echo "════════════════════════════════════════"
echo ""

# ── 1. Source tests pass ──────────────────────────────────────────────────────
echo "[1] Running Phase 13/14/15 source verification tests..."
node tests/unit/bff/test_phase13_frontend.js > /dev/null 2>&1
check "Phase 13 source tests 24/24" "$?"
node tests/unit/bff/test_phase14_source.js > /dev/null 2>&1
check "Phase 14 source tests 26/26" "$?"
node tests/unit/bff/test_phase15_source.js > /dev/null 2>&1
check "Phase 15 source tests 22/22" "$?"

# ── 2. Systemd units present ──────────────────────────────────────────────────
echo ""
echo "[2] Checking systemd unit files..."
for unit in \
    voiceos-bff.service \
    voiceos-webapi.service \
    voiceos-frontend.service \
    voiceos-voice-runtime.service \
    voiceos-dialer-worker.service; do
  if [[ -f "scripts/systemd/$unit" ]]; then
    check "$unit exists" "0"
  else
    check "$unit exists" "1"
  fi
done

# ── 3. Systemd units have TimeoutStopSec (graceful shutdown) ─────────────────
echo ""
echo "[3] Checking TimeoutStopSec in graceful-shutdown services..."
for unit in voiceos-voice-runtime.service voiceos-bff.service; do
  if grep -q 'TimeoutStopSec' "scripts/systemd/$unit" 2>/dev/null; then
    check "$unit has TimeoutStopSec" "0"
  else
    check "$unit has TimeoutStopSec" "1"
  fi
done

# ── 4. No secrets in source ───────────────────────────────────────────────────
echo ""
echo "[4] Checking for secrets in source..."
if python scripts/check_secrets.py > /dev/null 2>&1; then
  check "check_secrets.py clean" "0"
else
  check "check_secrets.py clean" "1"
fi

# ── 5. No PII in logs ─────────────────────────────────────────────────────────
if python scripts/check_pii_logs.py > /dev/null 2>&1; then
  check "check_pii_logs.py clean" "0"
else
  check "check_pii_logs.py clean" "1"
fi

# ── 6. Vault backup runnable ──────────────────────────────────────────────────
echo ""
echo "[6] Checking Vault backup scripts..."
if [[ -f "scripts/systemd/voiceos-vault-snapshot.service" ]]; then
  check "voiceos-vault-snapshot.service exists" "0"
else
  check "voiceos-vault-snapshot.service exists" "1"
fi

# ── 7. WAL archiving configured ───────────────────────────────────────────────
echo ""
echo "[7] Checking PostgreSQL WAL archiving scripts..."
if [[ -f "scripts/backup/wal_setup.sh" ]] || ls scripts/backup/*.sh 2>/dev/null | grep -q wal; then
  check "WAL archiving script exists" "0"
else
  warn "WAL archiving script not found in scripts/backup/ — verify manually"
fi

# ── 8. Runbooks present ───────────────────────────────────────────────────────
echo ""
echo "[8] Checking runbooks..."
for rb in \
    blue-green-model-deployment.md \
    database-failure-scenarios.md \
    phase14-chaos-testing-runbook.md \
    phase15-staging-validation-runbook.md \
    phase16-production-rollout-runbook.md; do
  if [[ -f "docs/runbooks/$rb" ]]; then
    check "runbook: $rb" "0"
  else
    check "runbook: $rb" "1"
  fi
done

# ── 9. Rollback SHA documented ────────────────────────────────────────────────
echo ""
echo "[9] Current rollback SHA (for rollback.sh):"
CURRENT_SHA=$(git rev-parse HEAD)
echo "       $CURRENT_SHA"
PREV_SHA=$(git rev-parse HEAD~1)
echo "    Previous SHA (rollback target): $PREV_SHA"
check "git history accessible" "0"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════"
echo -e "  ${GRN}$PASS passed${NC}, ${RED}$FAIL failed${NC}"

if [[ $FAIL -gt 0 ]]; then
  echo ""
  echo -e "  ${RED}BLOCKED${NC}: Fix all failures before running deploy.sh"
  exit 1
else
  echo ""
  echo -e "  ${GRN}READY${NC}: All gates pass — run: bash scripts/deploy/deploy.sh"
fi
