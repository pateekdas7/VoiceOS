#!/usr/bin/env bash
# VoiceOS v2 -- initiates a MongoDB restore drill (A9, complement to
# trigger-db-failover.sh's Postgres PITR drill).
#
# Non-destructive: takes a fresh mongodump of the live `voiceos` DB, restores
# it into a scratch DB named `voiceos_dr_drill`, and reports elapsed time.
# The live `voiceos` DB is NEVER touched. Timing captured to
# /tmp/voiceos_dr_mongo_elapsed_seconds for the DR report.
#
# Modes:
#   --mode point-in-copy  (default) mongodump → mongorestore into scratch DB.
#                         Proves the backup ROUND-TRIPS. No PITR because Mongo
#                         Community lacks continuous WAL-style archiving; a
#                         proper PITR drill requires oplog tailing which is
#                         out of scope for the pilot pen-test topology.
#   --mode oplog-replay   Reserved for future replica-set topology (once
#                         MongoDB is deployed with a real oplog-tailing
#                         backup agent). Currently exits 2.
#
# Usage:
#   MONGO_PASSWORD=<...> bash infra/dr/scripts/trigger-mongo-restore.sh
#   # or: source <(python3 scripts/vault/gen_env.py) && bash ...
#
# Companion: infra/dr/runbooks/mongo-restore.md
set -euo pipefail

MODE="point-in-copy"
BACKUP_DIR="${MONGO_BACKUP_DIR:-/opt/voiceos/backups/mongodb}"
LIVE_DB="${MONGO_DB:-voiceos}"
DRILL_DB="voiceos_dr_drill"
MONGO_HOST="${MONGO_HOST:-127.0.0.1}"
MONGO_PORT="${MONGO_PORT:-27017}"
MONGO_USER="${MONGO_USER:-voiceos}"
# MONGO_PASSWORD must be set in env or exported by gen_env.py.

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --backup-dir) BACKUP_DIR="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

log() { echo "[trigger-mongo-restore] $(date -u +%FT%TZ) $*"; }

: "${MONGO_PASSWORD:?MONGO_PASSWORD must be set (source scripts/vault/gen_env.py first)}"

case "$MODE" in
  point-in-copy)
    log "Starting MongoDB restore drill (live DB '${LIVE_DB}' → scratch DB '${DRILL_DB}')."
    START_TS=$(date +%s)

    mkdir -p "${BACKUP_DIR}"
    DUMP_DIR="${BACKUP_DIR}/drill-$(date +%Y%m%d%H%M%S)"

    log "Taking a fresh dump of '${LIVE_DB}' into ${DUMP_DIR}..."
    mongodump \
      --host "${MONGO_HOST}" --port "${MONGO_PORT}" \
      --username "${MONGO_USER}" --password "${MONGO_PASSWORD}" \
      --authenticationDatabase "${LIVE_DB}" \
      --db "${LIVE_DB}" \
      --out "${DUMP_DIR}" \
      --gzip \
      --quiet

    if [[ ! -d "${DUMP_DIR}/${LIVE_DB}" ]]; then
      log "ERROR: mongodump produced no output under ${DUMP_DIR}/${LIVE_DB}. Aborting."
      exit 1
    fi

    log "Dropping any prior '${DRILL_DB}' scratch DB (idempotent)..."
    mongosh \
      --host "${MONGO_HOST}" --port "${MONGO_PORT}" \
      --username "${MONGO_USER}" --password "${MONGO_PASSWORD}" \
      --authenticationDatabase "${LIVE_DB}" \
      --quiet \
      --eval "db.getSiblingDB('${DRILL_DB}').dropDatabase()" >/dev/null

    log "Restoring dump into '${DRILL_DB}' (using --nsFrom/--nsTo — live DB untouched)..."
    mongorestore \
      --host "${MONGO_HOST}" --port "${MONGO_PORT}" \
      --username "${MONGO_USER}" --password "${MONGO_PASSWORD}" \
      --authenticationDatabase "${LIVE_DB}" \
      --nsFrom "${LIVE_DB}.*" \
      --nsTo "${DRILL_DB}.*" \
      --gzip \
      --quiet \
      "${DUMP_DIR}"

    log "Verifying collection counts round-trip..."
    LIVE_COLLS=$(mongosh --host "${MONGO_HOST}" --port "${MONGO_PORT}" \
      --username "${MONGO_USER}" --password "${MONGO_PASSWORD}" \
      --authenticationDatabase "${LIVE_DB}" --quiet \
      --eval "db.getSiblingDB('${LIVE_DB}').getCollectionNames().length")
    DRILL_COLLS=$(mongosh --host "${MONGO_HOST}" --port "${MONGO_PORT}" \
      --username "${MONGO_USER}" --password "${MONGO_PASSWORD}" \
      --authenticationDatabase "${LIVE_DB}" --quiet \
      --eval "db.getSiblingDB('${DRILL_DB}').getCollectionNames().length")

    if [[ "${LIVE_COLLS}" != "${DRILL_COLLS}" ]]; then
      log "ERROR: collection count mismatch — live=${LIVE_COLLS}, drill=${DRILL_COLLS}. Restore incomplete."
      exit 1
    fi
    log "Collection count round-trip: live=${LIVE_COLLS}, drill=${DRILL_COLLS} — PASS."

    END_TS=$(date +%s)
    ELAPSED=$(( END_TS - START_TS ))
    echo "${ELAPSED}" > /tmp/voiceos_dr_mongo_elapsed_seconds
    log "MongoDB restore drill complete in ${ELAPSED}s (target: <= 300s for pilot dataset)."
    log "Scratch DB '${DRILL_DB}' left in place for manual spot-check. Drop it with:"
    log "  mongosh --eval \"db.getSiblingDB('${DRILL_DB}').dropDatabase()\""
    ;;

  oplog-replay)
    log "Oplog-replay mode requires a replica-set topology with a continuous"
    log "oplog-tailing backup agent (e.g. Percona Backup for MongoDB). Not"
    log "applicable to the single-node standalone deployment. Exiting 2."
    exit 2
    ;;

  *)
    echo "Unknown --mode: ${MODE}" >&2
    exit 1
    ;;
esac
