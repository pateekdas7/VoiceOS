#!/usr/bin/env bash
# Phase 11c — MongoDB daily dump
#
# Dumps all VoiceOS MongoDB collections to /backup/mongodb/YYYYMMDD/.
# Retains 30 days locally. Syncs to offsite (MinIO or S3).
#
# Triggered by voiceos-mongodb-backup.timer (daily 01:30 UTC).
#
# NOTE: Execution deferred until CPU node is available (Phase 11 skip policy).

set -euo pipefail

DATE=$(date +%Y%m%d)
BACKUP_DIR="/backup/mongodb/${DATE}"
RETENTION_DAYS=30
MONGO_URI="${MONGODB_URL:-mongodb://localhost:27017}"
MINIO_BUCKET="${MINIO_BUCKET_BACKUPS:-voiceos-backups}"

echo "=== VoiceOS Phase 11c: MongoDB dump — ${DATE} ==="

mkdir -p "${BACKUP_DIR}"

# 1. Dump all collections
mongodump \
  --uri="${MONGO_URI}" \
  --db=voiceos \
  --out="${BACKUP_DIR}" \
  --gzip

echo "Dump complete: ${BACKUP_DIR}"
du -sh "${BACKUP_DIR}"

# 2. Sync to offsite (MinIO / S3-compatible)
if command -v mc &>/dev/null; then
  mc cp --recursive "${BACKUP_DIR}" "voiceos-backup/${MINIO_BUCKET}/mongodb/${DATE}/"
  echo "Synced to s3:${MINIO_BUCKET}/mongodb/${DATE}/"
else
  echo "WARN: mc not found — skipping offsite sync. Install MinIO client for offsite backup."
fi

# 3. Prune local backups older than 30 days
find /backup/mongodb -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" -exec rm -rf {} + 2>/dev/null || true
echo "Pruned backups older than ${RETENTION_DAYS} days."

echo "=== MongoDB backup complete ==="
