#!/usr/bin/env bash
# Phase 11a — PostgreSQL restore from WAL archive
#
# Point-in-time recovery from a base backup + WAL stream.
# RTO target: 2 hours.
#
# USAGE:
#   sudo bash scripts/backup/pg_restore_from_wal.sh --target-time "2026-09-17 14:00:00 UTC"
#
# NOTE: Execution deferred until CPU node is available (Phase 11 skip policy).

set -euo pipefail

TARGET_TIME="${1:-}"
PG_DATA="${PGDATA:-/var/lib/postgresql/16/main}"
RESTORE_DIR="/var/lib/postgresql/16/restore"
MINIO_BUCKET="${MINIO_BUCKET_BACKUPS:-voiceos-backups}"

if [[ -z "${TARGET_TIME}" ]]; then
  echo "Usage: $0 \"YYYY-MM-DD HH:MM:SS UTC\""
  exit 1
fi

echo "=== VoiceOS Phase 11a: PostgreSQL PITR restore ==="
echo "Target time: ${TARGET_TIME}"
echo ""

# 1. Stop Postgres
systemctl stop postgresql
echo "Postgres stopped."

# 2. Download latest base backup from MinIO
echo "Downloading base backup..."
mkdir -p "${RESTORE_DIR}"
mc cp "voiceos-backup/${MINIO_BUCKET}/basebackup/latest/base.tar.gz" /tmp/pg_base.tar.gz
tar -xzf /tmp/pg_base.tar.gz -C "${RESTORE_DIR}"
chown -R postgres:postgres "${RESTORE_DIR}"

# 3. Write recovery config
cat > "${RESTORE_DIR}/recovery.conf" << EOF
restore_command = 'mc cp voiceos-backup/${MINIO_BUCKET}/wal/%f %p'
recovery_target_time = '${TARGET_TIME}'
recovery_target_action = 'promote'
EOF
chown postgres:postgres "${RESTORE_DIR}/recovery.conf"

# 4. Swap data directories
mv "${PG_DATA}" "${PG_DATA}.old.$(date +%Y%m%d%H%M%S)"
mv "${RESTORE_DIR}" "${PG_DATA}"

# 5. Start Postgres in recovery mode
systemctl start postgresql
echo "Postgres starting in recovery mode..."

# 6. Wait for recovery to complete and verify row counts
TIMEOUT=120
for i in $(seq 1 ${TIMEOUT}); do
  if psql -U postgres -c "SELECT pg_is_in_recovery();" 2>/dev/null | grep -q "f"; then
    echo "Recovery complete after ${i}s."
    break
  fi
  sleep 1
done

# 7. Verify critical row counts
echo "=== Row count verification ==="
psql -U postgres -d voiceos << 'SQL'
SELECT 'campaigns'     AS tbl, COUNT(*) FROM campaigns
UNION ALL
SELECT 'campaign_leads',        COUNT(*) FROM leads
UNION ALL
SELECT 'call_attempts',         COUNT(*) FROM call_attempts
UNION ALL
SELECT 'promises_to_pay',       COUNT(*) FROM promises_to_pay;
SQL

echo ""
echo "=== Restore complete. Verify data looks correct before re-enabling traffic. ==="
