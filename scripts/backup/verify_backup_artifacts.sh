#!/usr/bin/env bash
set -euo pipefail

# VoiceOS Level-2 backup artifact verification.
# Read-only verification of the existing backup mechanisms; does not perform
# restore or destructive operations. Restore verification remains a runtime gate.

failures=0
log() { printf '[%s] BACKUP_VERIFY %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
pass() { log "PASS $*"; }
fail() { log "FAIL $*"; failures=$((failures + 1)); }

MAX_AGE_HOURS="${BACKUP_MAX_AGE_HOURS:-26}"
MONGO_BACKUP_ROOT="${MONGO_BACKUP_ROOT:-/backup/mongodb}"
VAULT_BACKUP_ROOT="${VAULT_BACKUP_ROOT:-/backup/vault}"

if [[ -d "${MONGO_BACKUP_ROOT}" ]]; then
  latest_mongo="$(find "${MONGO_BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2- || true)"
  if [[ -n "${latest_mongo}" && -d "${latest_mongo}" ]]; then
    age=$(( $(date +%s) - $(stat -c %Y "${latest_mongo}") ))
    if (( age <= MAX_AGE_HOURS * 3600 )); then pass "MongoDB backup artifact is ${age}s old: ${latest_mongo}"; else fail "MongoDB backup artifact is older than ${MAX_AGE_HOURS}h: ${latest_mongo}"; fi
  else fail "No MongoDB backup directory found under ${MONGO_BACKUP_ROOT}"; fi
else fail "MongoDB backup root missing: ${MONGO_BACKUP_ROOT}"; fi

if [[ -d "${VAULT_BACKUP_ROOT}" ]]; then
  latest_vault="$(find "${VAULT_BACKUP_ROOT}" -maxdepth 1 -type f -name '*.snap' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2- || true)"
  if [[ -n "${latest_vault}" && -s "${latest_vault}" ]]; then
    age=$(( $(date +%s) - $(stat -c %Y "${latest_vault}") ))
    if (( age <= MAX_AGE_HOURS * 3600 )); then pass "Vault snapshot is ${age}s old: ${latest_vault}"; else fail "Vault snapshot is older than ${MAX_AGE_HOURS}h: ${latest_vault}"; fi
  else fail "No non-empty Vault snapshot found under ${VAULT_BACKUP_ROOT}"; fi
else fail "Vault backup root missing: ${VAULT_BACKUP_ROOT}"; fi

if command -v redis-cli >/dev/null 2>&1; then
  redis_args=(-h "${REDIS_HOST:-127.0.0.1}" -p "${REDIS_PORT:-6379}")
  [[ -n "${REDIS_PASSWORD:-}" ]] && redis_args+=(-a "${REDIS_PASSWORD}")
  if [[ "$(redis-cli "${redis_args[@]}" ping 2>/dev/null)" == "PONG" ]]; then
    appendonly="$(redis-cli "${redis_args[@]}" CONFIG GET appendonly 2>/dev/null | tail -1)"
    aof_status="$(redis-cli "${redis_args[@]}" INFO persistence 2>/dev/null | awk -F: '$1=="aof_last_write_status" {print $2}' | tr -d '\r')"
    [[ "${appendonly}" == "yes" ]] && pass "Redis AOF enabled" || fail "Redis AOF is not enabled"
    [[ "${aof_status}" == "ok" || -z "${aof_status}" ]] && pass "Redis AOF write status is ${aof_status:-not reported}" || fail "Redis AOF write status=${aof_status}"
  else fail "Redis is unreachable for persistence verification"; fi
else fail "redis-cli is not installed"; fi

if command -v psql >/dev/null 2>&1; then
  export PGPASSWORD="${POSTGRES_PASSWORD:-}"
  archiver="$(psql -h "${POSTGRES_HOST:-127.0.0.1}" -p "${POSTGRES_PORT:-5432}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc "SELECT COALESCE(last_archived::text, '') || '|' || failed_count FROM pg_stat_archiver;" 2>/dev/null || true)"
  if [[ -n "${archiver}" ]]; then
    last_archived="${archiver%%|*}"
    failed_count="${archiver##*|}"
    [[ -n "${last_archived}" ]] && pass "PostgreSQL WAL archiver last_archived=${last_archived}" || fail "PostgreSQL WAL archiver has no last_archived timestamp"
    [[ "${failed_count}" == "0" ]] && pass "PostgreSQL WAL archiver failed_count=0" || fail "PostgreSQL WAL archiver failed_count=${failed_count}"
  else fail "PostgreSQL WAL archiver status unavailable"; fi
else fail "psql is not installed"; fi

if (( failures > 0 )); then
  log "FAIL backup verification completed with ${failures} failure(s)"
  exit 1
fi
log "PASS backup verification completed"
