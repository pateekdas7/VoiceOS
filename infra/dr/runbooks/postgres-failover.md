# Runbook: PostgreSQL Failover

**RTO target:** ≤ 30 min (Sprint-027.md; the DR drill's own Postgres sub-component targets standby promotion within 60s).
**RPO target:** ≤ 5 min (ROADMAP.md's PITR requirement).

## Scope

VoiceOS's current single-node CPU node topology (`deployment/CPU_NODE_STATE.md`) runs PostgreSQL as a single native process, not yet a managed Multi-AZ RDS/Cloud SQL instance (that is `infra/terraform/modules/database`'s target topology for a real cloud deployment, still `dev`-environment-only per Sprint-026 Phase 1). This runbook covers both:

1. **Today's actual topology** — single-node Postgres, recovered via PITR restore (this is what `infra/dr/scripts/trigger-db-failover.sh` exercises for real against the CPU node).
2. **The target managed topology** (Terraform `production` environment) — Multi-AZ RDS/Cloud SQL standby promotion, once provisioned.

## Detection

1. Prometheus `NodeDown`/`ErrorRateSpike` alerts fire, or `deployment/cpu/healthcheck.sh`'s Postgres check fails.
2. Confirm with a direct connection attempt:
   ```bash
   PGPASSWORD=<from-vault> psql -h localhost -U voiceos -d voiceos -c "SELECT 1;"
   ```

## Procedure — single-node topology (current)

1. Identify the last good base backup + WAL archive (`pg_basebackup` + `archive_command`, configured per `infra/dr/scripts/trigger-db-failover.sh`'s PITR setup).
2. Stop the failed `postgresql` service:
   ```bash
   systemctl stop postgresql
   ```
3. Restore the base backup to the data directory and replay WAL up to the last consistent point (Point-In-Time Recovery):
   ```bash
   bash infra/dr/scripts/trigger-db-failover.sh --mode pitr-restore
   ```
4. Start Postgres and verify it reaches a consistent, read-write state:
   ```bash
   systemctl start postgresql
   bash infra/dr/scripts/verify-recovery.sh --component postgres
   ```
5. Re-run `alembic current` to confirm the migration head matches the pre-failure state (no partial-migration state).
6. Re-run the full regression suite against the recovered instance.

## Procedure — target managed topology (Multi-AZ, once provisioned)

1. Managed failover is automatic (RDS/Cloud SQL detects primary failure and promotes the standby within its own SLA, typically 60–120s).
2. Application reconnects using the same DNS-stable endpoint (`POSTGRES_HOST` is never the raw instance IP — this is exactly why `infra/terraform/modules/database` provisions a stable endpoint, not a bare IP).
3. Verify: `bash infra/dr/scripts/verify-recovery.sh --component postgres`.
4. No application code change or restart is required if the connection pool retries on a stale connection (standard `psycopg2` reconnect-on-error behavior, already relied upon by every repository in `src/libs/repositories/`).

## Rollback

If the restored/promoted instance fails verification, do not point traffic at it. Fall back to the last-known-good snapshot and repeat. Never delete the failed instance's data directory until `verify-recovery.sh` passes cleanly on the recovered target.

## Post-incident

- Record actual RTO/RPO achieved in `infra/dr/DR_DRILL_REPORT_<date>.md`.
- File a BACKLOG.md technical-debt entry if RTO/RPO targets were missed.
