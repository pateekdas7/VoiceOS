#!/usr/bin/env bash
# scripts/dev/run_migrations.sh — Apply all Sprint-002 DDL files to Postgres.
#
# All migrations use IF NOT EXISTS — safe to run on an already-migrated schema.
#
# Usage:
#   bash scripts/dev/run_migrations.sh
#   bash scripts/dev/run_migrations.sh postgresql://user:pass@host/db
#
# Architecture: V3 Ch5 (Persistent Storage); DocSuite-09.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

log() { echo "[run-migrations] $*"; }
err() { echo "[run-migrations] ERROR: $*" >&2; exit 1; }

cd "$PROJECT_ROOT"

POSTGRES_DSN="${1:-${POSTGRES_DSN:-postgresql://voiceos:voiceos_dev_pw@localhost:5432/voiceos_dev}}"

if ! command -v psql >/dev/null 2>&1; then
    err "psql not found. Install postgresql-client."
fi

log "Target: $POSTGRES_DSN"
log "Applying Sprint-002 migrations (idempotent — IF NOT EXISTS)..."

MIGRATION_DIR="$PROJECT_ROOT/scripts/db/migrations"

for f in "$MIGRATION_DIR"/*.sql; do
    fname="$(basename "$f")"
    log "  $fname..."
    psql "$POSTGRES_DSN" -f "$f" -q
done

log "=== Migrations complete. ==="
