#!/usr/bin/env bash
# scripts/dev-down.sh — Stop and remove all Docker Compose services and volumes.
#
# WARNING: The -v flag removes named volumes (all local dev data is lost).
# Use 'docker compose stop' instead if you want to preserve volumes.
#
# Usage:
#   bash scripts/dev-down.sh        # stop + remove volumes
#   bash scripts/dev-down.sh --keep # stop only (keep volumes)
#
# Architecture: DocSuite-09.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

log() { echo "[dev-down] $*"; }

cd "$PROJECT_ROOT"

KEEP_VOLUMES=false
for arg in "$@"; do
    case "$arg" in
        --keep) KEEP_VOLUMES=true ;;
    esac
done

if [ "$KEEP_VOLUMES" = "true" ]; then
    log "Stopping services (keeping volumes)..."
    docker compose stop
    log "Done. Volumes preserved."
else
    log "Stopping services and removing volumes..."
    docker compose down -v
    log "Done. All volumes removed."
fi
