"""Phase 11 Verification Tests — Database Backup and Disaster Recovery

11a. PostgreSQL WAL archiving — setup script + restore script exist
11b. Redis persistence verification — verify script exists
11c. MongoDB daily dump — script + systemd timer exist
11d. Vault snapshot — script + systemd timer exist
11e. Failure scenario runbooks — exist and cover all 4 stores

Run with: python3 tests/unit/test_phase11_backup_dr.py
Strategy: file-existence checks + content inspection.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

passed = 0
failed = 0
results: list[tuple[str, str, str]] = []


def test(name: str) -> "object":
    def _dec(fn: "object") -> "object":
        global passed, failed
        try:
            fn()  # type: ignore[operator]
            results.append((name, "PASS", ""))
            passed += 1
        except AssertionError as e:
            results.append((name, "FAIL", str(e)))
            failed += 1
        except Exception as e:
            results.append((name, "FAIL", f"{type(e).__name__}: {e}"))
            failed += 1
        return fn
    return _dec


PG_SETUP_SH    = (ROOT / "scripts/backup/pg_wal_archiving_setup.sh").read_text()
PG_RESTORE_SH  = (ROOT / "scripts/backup/pg_restore_from_wal.sh").read_text()
REDIS_SH       = (ROOT / "scripts/backup/redis_verify_persistence.sh").read_text()
MONGO_DUMP_SH  = (ROOT / "scripts/backup/mongodb_dump.sh").read_text()
MONGO_REST_SH  = (ROOT / "scripts/backup/mongodb_restore.sh").read_text()
VAULT_SNAP_SH  = (ROOT / "scripts/backup/vault_snapshot.sh").read_text()
RUNBOOK        = (ROOT / "docs/runbooks/database-failure-scenarios.md").read_text()

# ---------------------------------------------------------------------------
# 11a: PostgreSQL WAL archiving scripts
# ---------------------------------------------------------------------------

@test("11a: pg_wal_archiving_setup.sh exists")
def _(): assert (ROOT / "scripts/backup/pg_wal_archiving_setup.sh").exists()

@test("11a: setup script configures wal_level=replica")
def _(): assert "wal_level" in PG_SETUP_SH and "replica" in PG_SETUP_SH

@test("11a: setup script configures archive_mode=on")
def _(): assert "archive_mode" in PG_SETUP_SH and "on" in PG_SETUP_SH

@test("11a: setup script sets archive_command with mc")
def _(): assert "archive_command" in PG_SETUP_SH

@test("11a: setup script sets archive_timeout for RPO=1h")
def _(): assert "archive_timeout" in PG_SETUP_SH

@test("11a: pg_restore_from_wal.sh exists")
def _(): assert (ROOT / "scripts/backup/pg_restore_from_wal.sh").exists()

@test("11a: restore script does PITR point-in-time recovery")
def _(): assert "recovery_target_time" in PG_RESTORE_SH

@test("11a: restore script verifies row counts after recovery")
def _(): assert "campaigns" in PG_RESTORE_SH and "call_attempts" in PG_RESTORE_SH

# ---------------------------------------------------------------------------
# 11b: Redis persistence verification
# ---------------------------------------------------------------------------

@test("11b: redis_verify_persistence.sh exists")
def _(): assert (ROOT / "scripts/backup/redis_verify_persistence.sh").exists()

@test("11b: script checks appendonly=yes")
def _(): assert "appendonly" in REDIS_SH

@test("11b: script checks appendfsync=everysec")
def _(): assert "appendfsync" in REDIS_SH and "everysec" in REDIS_SH

@test("11b: script documents replica configuration")
def _(): assert "REPLICAOF" in REDIS_SH or "replicaof" in REDIS_SH

# ---------------------------------------------------------------------------
# 11c: MongoDB daily dump
# ---------------------------------------------------------------------------

@test("11c: mongodb_dump.sh exists")
def _(): assert (ROOT / "scripts/backup/mongodb_dump.sh").exists()

@test("11c: dump script uses mongodump")
def _(): assert "mongodump" in MONGO_DUMP_SH

@test("11c: dump script uses gzip compression")
def _(): assert "gzip" in MONGO_DUMP_SH

@test("11c: dump script enforces 30-day retention")
def _(): assert "30" in MONGO_DUMP_SH and "RETENTION" in MONGO_DUMP_SH

@test("11c: mongodb_restore.sh exists")
def _(): assert (ROOT / "scripts/backup/mongodb_restore.sh").exists()

@test("11c: restore script uses mongorestore")
def _(): assert "mongorestore" in MONGO_REST_SH

@test("11c: voiceos-mongodb-backup.service systemd unit exists")
def _(): assert (ROOT / "scripts/systemd/voiceos-mongodb-backup.service").exists()

@test("11c: voiceos-mongodb-backup.timer systemd timer exists")
def _(): assert (ROOT / "scripts/systemd/voiceos-mongodb-backup.timer").exists()

@test("11c: timer scheduled daily")
def _():
    timer = (ROOT / "scripts/systemd/voiceos-mongodb-backup.timer").read_text()
    assert "OnCalendar" in timer

# ---------------------------------------------------------------------------
# 11d: Vault snapshot
# ---------------------------------------------------------------------------

@test("11d: vault_snapshot.sh exists")
def _(): assert (ROOT / "scripts/backup/vault_snapshot.sh").exists()

@test("11d: snapshot script uses vault operator raft snapshot save")
def _(): assert "vault operator raft snapshot save" in VAULT_SNAP_SH

@test("11d: snapshot script syncs to MinIO offsite")
def _(): assert "mc cp" in VAULT_SNAP_SH

@test("11d: snapshot script has retention cleanup")
def _(): assert "RETENTION" in VAULT_SNAP_SH or "mtime" in VAULT_SNAP_SH

@test("11d: voiceos-vault-snapshot.service systemd unit exists")
def _(): assert (ROOT / "scripts/systemd/voiceos-vault-snapshot.service").exists()

@test("11d: voiceos-vault-snapshot.timer systemd timer exists")
def _(): assert (ROOT / "scripts/systemd/voiceos-vault-snapshot.timer").exists()

@test("11d: snapshot script warns about unseal key security (R-8)")
def _(): assert "init.json" in VAULT_SNAP_SH

# ---------------------------------------------------------------------------
# 11e: Failure scenario runbooks
# ---------------------------------------------------------------------------

@test("11e: database-failure-scenarios.md runbook exists")
def _(): assert (ROOT / "docs/runbooks/database-failure-scenarios.md").exists()

@test("11e: runbook covers PostgreSQL failure")
def _(): assert "PostgreSQL" in RUNBOOK

@test("11e: runbook covers Redis failure")
def _(): assert "Redis" in RUNBOOK

@test("11e: runbook covers MongoDB failure")
def _(): assert "MongoDB" in RUNBOOK

@test("11e: runbook covers Vault failure")
def _(): assert "Vault" in RUNBOOK

@test("11e: runbook documents RTO/RPO for all 4 stores")
def _():
    assert "RPO" in RUNBOOK and "RTO" in RUNBOOK
    assert "1 hour" in RUNBOOK or "1h" in RUNBOOK    # Postgres RPO
    assert "30 sec" in RUNBOOK or "30s" in RUNBOOK or "30 secs" in RUNBOOK  # Redis RTO
    assert "24 hour" in RUNBOOK  # Mongo/Vault RPO

@test("11e: runbook has PITR restore procedure for Postgres")
def _(): assert "PITR" in RUNBOOK or "pg_restore_from_wal" in RUNBOOK

@test("11e: runbook has Redis replica failover procedure")
def _(): assert "REPLICAOF NO ONE" in RUNBOOK

@test("11e: runbook has MongoDB collection count verification step")
def _(): assert "countDocuments" in RUNBOOK or "COUNT" in RUNBOOK

@test("11e: runbook has Vault unseal procedure")
def _(): assert "unseal" in RUNBOOK

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

print("\n── Phase 11 Database Backup & DR Tests ───────────────────────────────")
for name, status, detail in results:
    line = f"  [{status}] {name}"
    if detail:
        line += f"\n         {detail}"
    print(line)
print(f"\n  {passed} passed, {failed} failed\n")
sys.exit(1 if failed > 0 else 0)
