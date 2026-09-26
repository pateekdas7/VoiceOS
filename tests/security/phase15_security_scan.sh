#!/usr/bin/env bash
# Phase 15 Security Scan
# Runs gitleaks, trufflehog, and OWASP ZAP against the staging environment.
# Requires: gitleaks, trufflehog (optional), docker (for ZAP)
#
# Usage:
#   STAGING_URL=http://localhost:8100 bash tests/security/phase15_security_scan.sh

set -euo pipefail

STAGING_URL="${STAGING_URL:-http://localhost:8100}"
REPORT_DIR="reports/security/phase15"
mkdir -p "$REPORT_DIR"

PASS=0
FAIL=0

check() {
  local name="$1"
  local ok="$2"
  if [[ "$ok" == "0" ]]; then
    echo "  ✓ $name"
    PASS=$((PASS + 1))
  else
    echo "  ✗ $name"
    FAIL=$((FAIL + 1))
  fi
}

echo ""
echo "Phase 15 Security Scan"
echo "══════════════════════"
echo ""

# ── 1. Gitleaks — no secrets in git history ───────────────────────────────────
echo "[1/3] Gitleaks — scanning git history for secrets..."
if command -v gitleaks &>/dev/null; then
  if gitleaks detect --source . --report-format json --report-path "$REPORT_DIR/gitleaks.json" --exit-code 1 2>/dev/null; then
    check "gitleaks: no secrets found" "0"
  else
    check "gitleaks: secrets detected" "1"
    echo "      Report: $REPORT_DIR/gitleaks.json"
  fi
else
  echo "  ⚠ gitleaks not installed — skipping"
  echo "    Install: https://github.com/gitleaks/gitleaks#installing"
fi

# ── 2. Trufflehog — deep secrets scan ─────────────────────────────────────────
echo "[2/3] Trufflehog — deep secrets scan..."
if command -v trufflehog &>/dev/null; then
  if trufflehog filesystem . --json --output "$REPORT_DIR/trufflehog.json" --fail 2>/dev/null; then
    check "trufflehog: no high-confidence secrets" "0"
  else
    check "trufflehog: potential secrets found" "1"
    echo "      Report: $REPORT_DIR/trufflehog.json"
  fi
else
  echo "  ⚠ trufflehog not installed — skipping"
  echo "    Install: pip install trufflehog3"
fi

# ── 3. OWASP ZAP baseline scan ────────────────────────────────────────────────
echo "[3/3] OWASP ZAP — baseline scan against $STAGING_URL..."
if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
  docker run --rm \
    -v "$(pwd)/$REPORT_DIR:/zap/wrk/:rw" \
    ghcr.io/zaproxy/zaproxy:stable zap-baseline.py \
    -t "$STAGING_URL" \
    -r zap-report.html \
    -J zap-report.json \
    -I 2>/dev/null
  exit_code=$?
  cp "$REPORT_DIR/zap-report.html" "$REPORT_DIR/" 2>/dev/null || true
  if [[ $exit_code -eq 0 ]]; then
    check "OWASP ZAP: no high-risk alerts" "0"
  else
    check "OWASP ZAP: alerts found" "1"
    echo "      Report: $REPORT_DIR/zap-report.html"
  fi
else
  echo "  ⚠ docker not available — skipping OWASP ZAP"
  echo "    OWASP ZAP requires Docker: https://www.zaproxy.org/docs/docker/baseline-scan/"
fi

# ── 4. No secrets in logs check ───────────────────────────────────────────────
echo "[opt] Checking logs for potential secret leaks..."
if python scripts/check_pii_logs.py 2>/dev/null; then
  check "PII/secret check in logs" "0"
else
  check "PII/secret check in logs" "1"
fi

echo ""
echo "  $PASS passed, $FAIL failed"
echo "  Reports: $REPORT_DIR/"
echo ""

[[ $FAIL -gt 0 ]] && exit 1 || exit 0
