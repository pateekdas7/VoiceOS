#!/usr/bin/env bash
# VoiceOS v2 -- post-failover health verification (Sprint-027).
#
# Run after any of infra/dr/runbooks/*.md's procedures (or
# trigger-db-failover.sh) to confirm the recovered component is genuinely
# healthy, not just "process is running". Exits non-zero on any failure,
# same contract as deployment/cpu/healthcheck.sh.
set -uo pipefail

COMPONENT="all"
FAILED=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --component) COMPONENT="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

log() { echo "[verify-recovery] $(date -u +%FT%TZ) $*"; }
check() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    log "OK   -- ${desc}"
  else
    log "FAIL -- ${desc}"
    FAILED=1
  fi
}

verify_postgres() {
  log "Verifying PostgreSQL recovery..."
  check "connection accepts queries" bash -c "PGPASSWORD=\"${POSTGRES_PASSWORD:-}\" psql -h localhost -U \"${POSTGRES_USER:-voiceos}\" -d \"${POSTGRES_DB:-voiceos}\" -c 'SELECT 1;'"
  # Deliberately not asserting a specific head number here (that class of
  # hardcoded-migration-head staleness has bitten nearly every prior
  # sprint's own tests, per CHANGELOG.md) -- just confirms alembic
  # resolves to *a* head cleanly, i.e. no partial/broken migration state.
  check "migration head resolves cleanly" bash -c "cd /opt/voiceos/app && POSTGRES_DSN=\"postgresql://${POSTGRES_USER:-voiceos}:${POSTGRES_PASSWORD:-}@localhost:5432/${POSTGRES_DB:-voiceos}\" /opt/voiceos/venv/bin/alembic current 2>/dev/null | grep -q head"
  # trigger-db-failover.sh's pitr-restore mode creates `voiceos_dr_drill`
  # for inspection -- clean it up here as the drill's own teardown step
  # (found live: an earlier draft of this check named a database that was
  # never actually created, `voiceos_dr_drill_leftover`, so it always
  # reported clean regardless of real state).
  PGPASSWORD="${POSTGRES_PASSWORD:-}" psql -h localhost -U "${POSTGRES_USER:-voiceos}" -d postgres -c "DROP DATABASE IF EXISTS voiceos_dr_drill;" >/dev/null 2>&1 || true
  check "drill database cleaned up" bash -c "! PGPASSWORD=\"${POSTGRES_PASSWORD:-}\" psql -h localhost -U \"${POSTGRES_USER:-voiceos}\" -d postgres -tAc \"SELECT 1 FROM pg_database WHERE datname='voiceos_dr_drill'\" | grep -q 1"
}

verify_redis() {
  log "Verifying Redis recovery..."
  check "PING succeeds" redis-cli -a "${REDIS_PASSWORD:-}" ping
  check "AOF persistence enabled" bash -c "redis-cli -a \"${REDIS_PASSWORD:-}\" CONFIG GET appendonly 2>/dev/null | grep -qi yes"
  # The `voiceos-events` stream/`main-group` consumer group only exist
  # once a real EventBus consumer has run at least once
  # (Consumer.__init__()'s ensure_consumer_group(), Sprint-013) -- on this
  # node today every service is still a health-stub (TT-006), so no
  # consumer has ever registered. Checked only if the stream exists at
  # all; absence is not itself a recovery failure (same "not yet
  # applicable" precedent as TT-002's own resolution notes).
  if redis-cli -a "${REDIS_PASSWORD:-}" EXISTS voiceos-events 2>/dev/null | grep -q '^1$'; then
    check "EventBus consumer group present" bash -c "redis-cli -a \"${REDIS_PASSWORD:-}\" XINFO GROUPS voiceos-events 2>/dev/null | grep -q main-group"
  else
    log "SKIP -- voiceos-events stream does not exist yet (no consumer has run on this node, TT-006)"
  fi
}

verify_gpu() {
  log "Verifying GPU node recovery (direct HTTP checks, no SSH)..."
  check "STT /health/ready" curl -sf "http://217.18.55.96:8100/health/ready"
  check "LLM /health" curl -sf "http://217.18.55.96:8000/health"
  check "TTS /health/ready" curl -sf "http://217.18.55.96:8200/health/ready"
}

verify_observability() {
  log "Verifying observability stack recovery..."
  check "Prometheus targets endpoint reachable" curl -sf "http://localhost:9090/api/v1/targets"
  check "Grafana health endpoint OK" curl -sf "http://localhost:3000/api/health"
  check "Loki ready" curl -sf "http://localhost:3100/ready"
  check "Jaeger query UI reachable" curl -sf "http://localhost:16686/jaeger"
}

case "$COMPONENT" in
  postgres) verify_postgres ;;
  redis) verify_redis ;;
  gpu) verify_gpu ;;
  observability) verify_observability ;;
  all)
    verify_postgres
    verify_redis
    verify_observability
    ;;
  *)
    echo "Unknown --component: ${COMPONENT}" >&2
    exit 1
    ;;
esac

if [[ "${FAILED}" -eq 0 ]]; then
  log "All checks passed for component=${COMPONENT}."
  exit 0
else
  log "One or more checks FAILED for component=${COMPONENT}."
  exit 1
fi
