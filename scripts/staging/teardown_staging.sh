#!/usr/bin/env bash
# Phase 15 — Staging Environment Teardown
# Stops staging services and optionally drops the staging database.
# Run as root: bash scripts/staging/teardown_staging.sh [--drop-db]

set -euo pipefail

DROP_DB=false
[[ "${1:-}" == "--drop-db" ]] && DROP_DB=true

STAGING_REDIS_PORT="${STAGING_REDIS_PORT:-6479}"
STAGING_DB="voiceos_staging"

echo "=== VoiceOS Staging Teardown ==="

echo "[1/3] Stopping staging bff.js..."
pkill -f "node bff.js.*8100" 2>/dev/null || true

echo "[2/3] Stopping staging web_api..."
pkill -f "uvicorn.*8101" 2>/dev/null || true

echo "[3/3] Stopping staging Redis..."
redis-cli -p "$STAGING_REDIS_PORT" shutdown nosave 2>/dev/null || true

if $DROP_DB; then
  echo "[opt] Dropping staging database: $STAGING_DB"
  sudo -u postgres dropdb --if-exists "$STAGING_DB"
fi

echo "=== Staging environment stopped ==="
