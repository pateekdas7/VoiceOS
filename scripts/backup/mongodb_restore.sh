#!/usr/bin/env bash
# Phase 11c — MongoDB restore from dump
#
# Restores from a dated backup directory. RTO target: 4 hours.
#
# USAGE:
#   bash scripts/backup/mongodb_restore.sh 20260917
#
# NOTE: Execution deferred until CPU node is available (Phase 11 skip policy).

set -euo pipefail

DATE="${1:-}"
BACKUP_DIR="/backup/mongodb/${DATE}"
MONGO_URI="${MONGODB_URL:-mongodb://localhost:27017}"
MINIO_BUCKET="${MINIO_BUCKET_BACKUPS:-voiceos-backups}"

if [[ -z "${DATE}" ]]; then
  echo "Usage: $0 YYYYMMDD"
  exit 1
fi

echo "=== VoiceOS Phase 11c: MongoDB restore from ${DATE} ==="

# 1. Download from MinIO if not present locally
if [[ ! -d "${BACKUP_DIR}" ]]; then
  echo "Local backup not found, downloading from MinIO..."
  mkdir -p "${BACKUP_DIR}"
  mc cp --recursive "voiceos-backup/${MINIO_BUCKET}/mongodb/${DATE}/" "${BACKUP_DIR}/"
fi

# 2. Restore (--drop drops the existing collection before restoring)
mongorestore \
  --uri="${MONGO_URI}" \
  --db=voiceos \
  --drop \
  --gzip \
  "${BACKUP_DIR}/voiceos"

# 3. Verify collection counts
echo "=== Collection counts after restore ==="
mongosh "${MONGO_URI}/voiceos" --eval '
  ["call_transcripts","decision_envelopes","conversation_states","ml_training_samples"]
  .forEach(c => print(c + ": " + db[c].countDocuments()));
'

echo ""
echo "=== Restore complete. Verify counts look correct before re-enabling traffic. ==="
