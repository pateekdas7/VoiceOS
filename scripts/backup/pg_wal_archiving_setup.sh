#!/usr/bin/env bash
# Phase 11a — PostgreSQL WAL archiving setup
#
# Configures WAL-level archiving to a local MinIO bucket so the DB can be
# restored to any 1-hour window (RPO target). Run once on the Postgres host
# as root or the postgres user.
#
# USAGE:
#   sudo bash scripts/backup/pg_wal_archiving_setup.sh
#
# PREREQUISITES:
#   - PostgreSQL 16 running natively (not in Docker)
#   - MinIO (or compatible) running at $MINIO_ENDPOINT
#   - mc (MinIO client) installed and configured with profile "voiceos-backup"
#   - Environment variables in /etc/voiceos/cpu.env:
#       MINIO_ENDPOINT, MINIO_BUCKET_BACKUPS, MINIO_ACCESS_KEY, MINIO_SECRET_KEY
#
# NOTE: Execution deferred until CPU node is available (Phase 11 skip policy).

set -euo pipefail

PG_DATA="${PGDATA:-/var/lib/postgresql/16/main}"
PG_CONF="${PG_DATA}/postgresql.conf"
MINIO_BUCKET="${MINIO_BUCKET_BACKUPS:-voiceos-backups}"
ARCHIVE_DIR="/backup/pg_wal"
WAL_SEGMENT_SIZE_MB=16

echo "=== VoiceOS Phase 11a: PostgreSQL WAL archiving setup ==="

# 1. Create local archive staging directory
mkdir -p "${ARCHIVE_DIR}"
chown postgres:postgres "${ARCHIVE_DIR}"

# 2. Patch postgresql.conf (idempotent via sed guards)
patch_pg_conf() {
  local key="$1" value="$2"
  if grep -q "^${key}" "${PG_CONF}"; then
    sed -i "s|^${key}.*|${key} = ${value}|" "${PG_CONF}"
  else
    echo "${key} = ${value}" >> "${PG_CONF}"
  fi
}

patch_pg_conf "wal_level"              "replica"
patch_pg_conf "archive_mode"           "on"
patch_pg_conf "archive_command"        "'mc cp %p voiceos-backup/${MINIO_BUCKET}/wal/%f'"
patch_pg_conf "archive_timeout"        "3600"   # force segment switch every hour (RPO = 1h)
patch_pg_conf "max_wal_senders"        "3"
patch_pg_conf "wal_keep_size"          "$((WAL_SEGMENT_SIZE_MB * 32))MB"

echo "postgresql.conf patched."

# 3. Reload Postgres to pick up archive settings (no restart needed)
pg_ctlcluster 16 main reload || systemctl reload postgresql
echo "Postgres reloaded."

# 4. Verify archiving is active
psql -U postgres -c "SELECT pg_walfile_name(pg_current_wal_lsn()), archive_mode FROM pg_stat_archiver, pg_settings WHERE name='archive_mode';" || true

echo ""
echo "=== Setup complete. Verify with: ==="
echo "  psql -U postgres -c \"SELECT * FROM pg_stat_archiver;\""
echo "  mc ls voiceos-backup/${MINIO_BUCKET}/wal/"
echo ""
echo "Estimated WAL volume: ~${WAL_SEGMENT_SIZE_MB}MB per segment, segments archived every 3600s."
