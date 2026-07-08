#!/usr/bin/env bash
# scripts/dev/reset_db.sh — Drop and recreate the voiceos_dev Postgres database.
#
# WARNING: Destroys all data in voiceos_dev. For development use only.
# Runs Sprint-002 migrations against the fresh database after recreation.
#
# Requires:
#   - Docker Compose Postgres service running (docker compose up -d postgres)
#   - OR POSTGRES_DSN pointing to a Postgres instance where you have superuser rights
#
# Usage:
#   bash scripts/dev/reset_db.sh
#
# Architecture: V6 Ch6 (Engineering Workflow); DocSuite-12.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

log() { echo "[reset-db] $*"; }
err() { echo "[reset-db] ERROR: $*" >&2; exit 1; }

cd "$PROJECT_ROOT"

ADMIN_DSN="${ADMIN_DSN:-postgresql://voiceos:voiceos_dev_pw@localhost:5432/postgres}"
TARGET_DB="voiceos_dev"

if ! command -v psql >/dev/null 2>&1; then
    err "psql not found. Install postgresql-client or add it to PATH."
fi

log "Dropping database: $TARGET_DB"
psql "$ADMIN_DSN" -c "DROP DATABASE IF EXISTS $TARGET_DB;" -q || true

log "Creating database: $TARGET_DB"
psql "$ADMIN_DSN" -c "CREATE DATABASE $TARGET_DB OWNER voiceos;" -q

log "Running Sprint-002 migrations..."
TARGET_DSN="${POSTGRES_DSN:-postgresql://voiceos:voiceos_dev_pw@localhost:5432/$TARGET_DB}"
bash "$PROJECT_ROOT/scripts/dev/run_migrations.sh" "$TARGET_DSN"

log "=== Database reset complete. ==="
