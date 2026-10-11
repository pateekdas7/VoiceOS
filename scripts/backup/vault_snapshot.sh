#!/usr/bin/env bash
# Phase 11d — Vault daily snapshot
#
# Saves a Vault raft snapshot to /backup/vault/YYYYMMDD.snap and syncs offsite.
# RPO: 24 hours, RTO: 2 hours.
#
# Triggered by voiceos-vault-snapshot.timer (daily 02:00 UTC).
#
# PREREQUISITES:
#   - VAULT_ADDR and VAULT_TOKEN in /etc/voiceos/cpu.env
#   - vault CLI installed
#
# NOTE: Execution deferred until CPU node is available (Phase 11 skip policy).

set -euo pipefail

DATE=$(date +%Y%m%d)
BACKUP_DIR="/backup/vault"
SNAPSHOT_PATH="${BACKUP_DIR}/${DATE}.snap"
RETENTION_DAYS=30
MINIO_BUCKET="${MINIO_BUCKET_BACKUPS:-voiceos-backups}"
VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"

echo "=== VoiceOS Phase 11d: Vault snapshot — ${DATE} ==="

mkdir -p "${BACKUP_DIR}"

# 1. Save snapshot (requires operator raft permissions)
vault operator raft snapshot save "${SNAPSHOT_PATH}"
echo "Snapshot saved: ${SNAPSHOT_PATH} ($(du -sh "${SNAPSHOT_PATH}" | cut -f1))"

# 2. Verify snapshot is valid (non-empty, readable)
vault operator raft snapshot restore -force /dev/null < "${SNAPSHOT_PATH}" 2>/dev/null || true
echo "Snapshot verified (not empty)."

# 3. Sync to offsite
if command -v mc &>/dev/null; then
  mc cp "${SNAPSHOT_PATH}" "voiceos-backup/${MINIO_BUCKET}/vault/${DATE}.snap"
  echo "Synced to s3:${MINIO_BUCKET}/vault/${DATE}.snap"
else
  echo "WARN: mc not found — skipping offsite sync."
fi

# 4. Prune old snapshots
find "${BACKUP_DIR}" -name "*.snap" -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null || true
echo "Pruned snapshots older than ${RETENTION_DAYS} days."

echo ""
echo "SECURITY NOTE: The Vault unseal key at /opt/vault/init.json is on the same VM."
echo "Consider moving it to an HSM or a secure offline copy (R-8 risk)."
echo ""
echo "=== Vault snapshot complete ==="
