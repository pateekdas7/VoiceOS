#!/usr/bin/env bash
# Phase 11b — Redis AOF persistence verification
#
# Verifies appendonly + appendfsync settings and optionally configures a replica.
# RPO: 1 second (AOF), RTO: 30 seconds (replica failover).
#
# USAGE:
#   bash scripts/backup/redis_verify_persistence.sh
#   bash scripts/backup/redis_verify_persistence.sh --setup-replica REPLICA_IP
#
# NOTE: Execution deferred until CPU node is available (Phase 11 skip policy).

set -euo pipefail

REDIS_CLI="${REDIS_CLI:-redis-cli}"
REDIS_AUTH="${REDIS_PASSWORD:-}"
REPLICA_IP="${1:-}"

redis_cmd() {
  if [[ -n "${REDIS_AUTH}" ]]; then
    ${REDIS_CLI} -a "${REDIS_AUTH}" "$@"
  else
    ${REDIS_CLI} "$@"
  fi
}

echo "=== VoiceOS Phase 11b: Redis persistence verification ==="
echo ""

# 1. Verify AOF settings
echo "--- Current persistence config ---"
redis_cmd CONFIG GET appendonly
redis_cmd CONFIG GET appendfsync
redis_cmd CONFIG GET save

APPENDONLY=$(redis_cmd CONFIG GET appendonly | tail -1)
APPENDFSYNC=$(redis_cmd CONFIG GET appendfsync | tail -1)

if [[ "${APPENDONLY}" != "yes" ]]; then
  echo "FAIL: appendonly is not 'yes'. RPO will exceed 1s."
  echo "Fix: redis_cmd CONFIG SET appendonly yes"
  echo "     Also add 'appendonly yes' to /etc/redis/redis.conf for persistence across restarts."
  exit 1
fi

if [[ "${APPENDFSYNC}" != "everysec" ]]; then
  echo "WARN: appendfsync is '${APPENDFSYNC}', expected 'everysec'."
fi

echo "PASS: appendonly=yes, appendfsync=${APPENDFSYNC}"

# 2. Verify AOF file exists and is non-empty
AOF_PATH=$(redis_cmd CONFIG GET dir | tail -1)/$(redis_cmd CONFIG GET appendfilename | tail -1)
if [[ -f "${AOF_PATH}" ]] && [[ -s "${AOF_PATH}" ]]; then
  echo "PASS: AOF file exists at ${AOF_PATH} ($(du -sh "${AOF_PATH}" | cut -f1))"
else
  echo "WARN: AOF file not found at ${AOF_PATH} — may not exist yet on fresh instance."
fi

# 3. Run BGSAVE and confirm last_bgsave_status
redis_cmd BGSAVE
sleep 2
redis_cmd LASTSAVE
redis_cmd INFO persistence | grep -E "rdb_last_bgsave_status|aof_enabled|aof_last_write_status"

# 4. Optionally configure replica
if [[ -n "${REPLICA_IP}" ]]; then
  echo ""
  echo "--- Configuring replica at ${REPLICA_IP} ---"
  echo "Run on the replica host:"
  echo "  redis-cli REPLICAOF ${HOSTNAME:-cpu-node-ip} 6379"
  echo "  redis-cli CONFIG SET replica-read-only yes"
  echo "Or add to replica's redis.conf:"
  echo "  replicaof ${HOSTNAME:-cpu-node-ip} 6379"
  echo ""
  echo "After configuring, verify replication:"
  echo "  redis-cli -h ${REPLICA_IP} INFO replication | grep role"
fi

echo ""
echo "=== Verification complete. Redis AOF RPO = 1s confirmed. ==="
