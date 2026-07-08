#!/usr/bin/env bash
# scripts/dev-up.sh — Start all Docker Compose services for local development.
#
# Starts Postgres, Redis, MongoDB, Prometheus, and Grafana; waits for all
# health checks to pass before returning.
#
# Usage:
#   bash scripts/dev-up.sh
#
# Architecture: V3 (Reliability); DocSuite-09 (Deployment Cookbook).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

log() { echo "[dev-up] $*"; }
err() { echo "[dev-up] ERROR: $*" >&2; exit 1; }

cd "$PROJECT_ROOT"

# Check Docker is running
if ! docker info >/dev/null 2>&1; then
    err "Docker is not running. Start Docker Desktop and try again."
fi

log "Starting VoiceOS development services..."
docker compose up -d

# Wait for services to pass health checks
log "Waiting for health checks..."

wait_healthy() {
    local service="$1"
    local max_wait="${2:-60}"
    local elapsed=0
    while [ $elapsed -lt $max_wait ]; do
        status=$(docker compose ps --format json "$service" 2>/dev/null \
            | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Health','unknown'))" \
            2>/dev/null || echo "unknown")
        if [ "$status" = "healthy" ]; then
            log "$service: healthy"
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    log "WARNING: $service health check timed out after ${max_wait}s (status: $status)"
    return 1
}

wait_healthy postgres 90
wait_healthy redis 60
wait_healthy mongodb 120

log ""
log "=== Services ready ==="
log "Postgres  : postgresql://voiceos:voiceos_dev_pw@localhost:5432/voiceos_dev"
log "Redis     : redis://localhost:6379/0"
log "MongoDB   : mongodb://localhost:27017/voiceos_dev"
log "Prometheus: http://localhost:9090"
log "Grafana   : http://localhost:3000"
log ""
log "Integration tests:"
log "  POSTGRES_DSN=postgresql://voiceos:voiceos_dev_pw@localhost:5432/voiceos_dev \\"
log "  REDIS_URL=redis://localhost:6379/0 \\"
log "  MONGODB_URI=mongodb://localhost:27017/voiceos_dev \\"
log "  pytest tests/integration/ -v"
