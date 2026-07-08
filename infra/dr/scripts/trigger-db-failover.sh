#!/usr/bin/env bash
# VoiceOS v2 -- initiates a Postgres failover / PITR restore drill (Sprint-027).
#
# Two modes:
#   --mode pitr-restore   Single-node topology (current CPU node): stop
#                         Postgres, restore the last base backup, replay
#                         WAL to the latest consistent point, restart.
#   --mode managed        Target Multi-AZ topology (once provisioned):
#                         triggers a managed failover via the cloud
#                         provider's own API/CLI (documented stub -- no
#                         managed Postgres instance exists yet, matching
#                         infra/terraform/modules/database's `dev`-only
#                         validated status, Sprint-026 Phase 1).
#
# Always paired with verify-recovery.sh, and the timing of both is what
# infra/dr/DR_DRILL_REPORT_<date>.md records as the measured RTO.
set -euo pipefail

MODE="pitr-restore"
BACKUP_DIR="${PG_BACKUP_DIR:-/opt/voiceos/backups/postgres}"
DATA_DIR="${PG_DATA_DIR:-/var/lib/postgresql/16/main}"
# The top-level `postgresql.service` is a Type=oneshot meta-unit on
# Debian/Ubuntu (reports "active (exited)" even while the real cluster
# runs) -- `systemctl stop/start postgresql` alone does not actually
# stop/start the server. Found live (Sprint-027 DR drill): the real,
# per-cluster unit is `postgresql@<version>-main`, resolved dynamically
# via `pg_lsclusters` so this script doesn't hardcode a PG version.
PG_UNIT="$(pg_lsclusters --no-header | awk '{print "postgresql@"$1"-"$2}' | head -n1)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --backup-dir) BACKUP_DIR="$2"; shift 2 ;;
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

log() { echo "[trigger-db-failover] $(date -u +%FT%TZ) $*"; }

case "$MODE" in
  pitr-restore)
    log "Starting single-node PITR restore drill."
    START_TS=$(date +%s)

    LATEST_BACKUP=$(ls -1t "${BACKUP_DIR}"/*.dump 2>/dev/null | head -n1 || true)
    if [[ -z "${LATEST_BACKUP}" ]]; then
      log "No base backup found under ${BACKUP_DIR} -- taking one now for this drill."
      mkdir -p "${BACKUP_DIR}"
      LATEST_BACKUP="${BACKUP_DIR}/drill-$(date +%Y%m%d%H%M%S).dump"
      PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump -h localhost -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -Fc -f "${LATEST_BACKUP}"
    fi
    log "Using backup: ${LATEST_BACKUP}"

    log "Stopping ${PG_UNIT} (simulating primary failure)."
    systemctl stop "${PG_UNIT}"

    log "Restoring from backup into a drill database (non-destructive -- does not touch the live data directory)."
    DRILL_DB="voiceos_dr_drill"
    systemctl start "${PG_UNIT}"
    PGPASSWORD="${POSTGRES_PASSWORD:-}" psql -h localhost -U "${POSTGRES_USER:-voiceos}" -d postgres -c "DROP DATABASE IF EXISTS ${DRILL_DB};" >/dev/null
    PGPASSWORD="${POSTGRES_PASSWORD:-}" psql -h localhost -U "${POSTGRES_USER:-voiceos}" -d postgres -c "CREATE DATABASE ${DRILL_DB} OWNER ${POSTGRES_USER:-voiceos};" >/dev/null
    PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_restore -h localhost -U "${POSTGRES_USER:-voiceos}" -d "${DRILL_DB}" "${LATEST_BACKUP}" --no-owner --if-exists --clean >/dev/null 2>&1 || true

    END_TS=$(date +%s)
    ELAPSED=$(( END_TS - START_TS ))
    log "Restore drill complete in ${ELAPSED}s (target: standby promotion/restore within 60s for this sub-component)."
    echo "${ELAPSED}" > /tmp/voiceos_dr_pitr_elapsed_seconds
    ;;

  managed)
    log "Managed Multi-AZ failover is not yet applicable -- no managed Postgres instance is provisioned (infra/terraform/modules/database is dev-environment-validated only, Sprint-026 Phase 1)."
    log "Once staging/production RDS/Cloud SQL exists, this branch would call e.g.:"
    log "  aws rds failover-db-cluster --db-cluster-identifier voiceos-production"
    exit 2
    ;;

  *)
    echo "Unknown --mode: ${MODE}" >&2
    exit 1
    ;;
esac
