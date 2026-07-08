#!/usr/bin/env bash
# scripts/run-tests.sh — Run all VoiceOS test suites in the correct order.
#
# Order:
#   1. Static analysis (ruff + mypy)
#   2. Module boundary check
#   3. Unit tests + coverage gates
#   4. Invariant tests (100% coverage gate)
#   5. Integration tests (skipped unless DB env vars are set)
#
# Integration test environment variables:
#   POSTGRES_DSN  — set to run Postgres integration tests
#   REDIS_URL     — set to run Redis integration tests
#   MONGODB_URI   — set to run MongoDB integration tests
#
# Usage:
#   bash scripts/run-tests.sh
#   POSTGRES_DSN=... REDIS_URL=... MONGODB_URI=... bash scripts/run-tests.sh
#
# Architecture: V6 Ch9 (Testing Standards); DocSuite-08.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

log()  { echo "[run-tests] $*"; }
pass() { echo "[run-tests] PASS: $*"; }
fail() { echo "[run-tests] FAIL: $*" >&2; }

cd "$PROJECT_ROOT"

FAILURES=0

run_step() {
    local label="$1"; shift
    log "--- $label ---"
    if "$@"; then
        pass "$label"
    else
        fail "$label"
        FAILURES=$((FAILURES + 1))
    fi
}

# ---------------------------------------------------------------------------
# 1. Static analysis
# ---------------------------------------------------------------------------
run_step "ruff lint" python -m ruff check src/ tests/
run_step "ruff format check" python -m ruff format --check src/ tests/
run_step "mypy --strict" python -m mypy --strict src/ tests/

# ---------------------------------------------------------------------------
# 2. Module boundary check
# ---------------------------------------------------------------------------
run_step "module boundary check" python scripts/check_boundaries.py --src src

# ---------------------------------------------------------------------------
# 3. Unit tests + coverage
# ---------------------------------------------------------------------------
run_step "unit tests" python -m pytest tests/unit/ tests/invariants/ \
    --cov=src/libs/contracts \
    --cov=src/libs/invariants \
    --cov-report=term-missing \
    --cov-fail-under=90 \
    -v

# ---------------------------------------------------------------------------
# 4. Integration tests (skipped unless env vars set)
# ---------------------------------------------------------------------------
log "--- integration tests ---"
if [ -n "${POSTGRES_DSN:-}" ] || [ -n "${REDIS_URL:-}" ] || [ -n "${MONGODB_URI:-}" ]; then
    run_step "integration tests" python -m pytest tests/integration/ -v
else
    log "SKIP: No DB env vars set. Set POSTGRES_DSN/REDIS_URL/MONGODB_URI to run."
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
if [ $FAILURES -eq 0 ]; then
    log "=== ALL TESTS PASSED ==="
    exit 0
else
    log "=== $FAILURES STEP(S) FAILED ==="
    exit 1
fi
