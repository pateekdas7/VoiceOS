# Database Failure Scenarios — Runbooks

**Scope:** VoiceOS production database recovery procedures for all four data stores.
**RTO / RPO targets:** PostgreSQL (RTO 2h / RPO 1h), Redis (RTO 30s / RPO 1s), MongoDB (RTO 4h / RPO 24h), Vault (RTO 2h / RPO 24h).

---

## 1. PostgreSQL Failure

### Scenario A — Primary process crash (OOM, segfault, kill -9)

**Symptoms:** `systemctl status postgresql` shows `failed`; bff.js returns 500s on all DB-backed routes.

**Impact:** All API reads and writes fail. Voice calls in progress are unaffected (voice runtime uses an existing connection pool — existing connections may linger briefly before failing).

**Recovery steps:**
```bash
# 1. Check disk space first — OOM kills often precede full-disk
df -h /var/lib/postgresql

# 2. Restart Postgres
systemctl restart postgresql

# 3. Watch logs for corruption
journalctl -u postgresql -f --since "5 minutes ago"

# 4. Verify Alembic head
psql -U voiceos -d voiceos -c "SELECT version_num FROM alembic_version;"

# 5. Re-enable traffic
systemctl reload nginx   # or bff.js restart if connection pool was exhausted
```

**RTO:** < 5 minutes for clean crash. Up to 30 minutes if crash caused index corruption (VACUUM/REINDEX).

---

### Scenario B — Full-disk / data corruption

**Symptoms:** Postgres fails to start; `pg_dump` fails; `VACUUM` errors.

**Recovery steps (PITR from WAL archive):**
```bash
# Requires WAL archiving active (Phase 11a)
# Pick a target time just before the corruption was detected
sudo bash scripts/backup/pg_restore_from_wal.sh "2026-09-17 13:45:00 UTC"
```

See `scripts/backup/pg_restore_from_wal.sh` for full procedure. **RTO: 2 hours.**

---

### Scenario C — Alembic migration failure (partial migration applied)

**Symptoms:** Service starts but a migration step errored mid-way; some columns missing.

**Recovery steps:**
```bash
# Check current head
psql -U voiceos -d voiceos -c "SELECT * FROM alembic_version;"

# Roll back last migration
alembic downgrade -1

# Re-apply cleanly
alembic upgrade head

# Verify
psql -U voiceos -d voiceos -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';"
```

---

## 2. Redis Failure

### Scenario A — Redis restart / OOM kill

**Symptoms:** All Redis-dependent features fail: dialer queue empty, login rate limiting bypassed, idempotency cache lost, session store unavailable.

**Impact on voice runtime:** Zero per-turn Redis reads — voice runtime is unaffected mid-call. Callback processing (LPUSH on completion) will fail until Redis recovers.

**Recovery steps:**
```bash
# 1. Restart Redis
systemctl restart redis-server

# 2. Verify AOF replay completed
redis-cli PING   # should return PONG within 5-10 seconds
redis-cli INFO persistence | grep aof_last_write_status  # should be "ok"

# 3. Check for data loss
redis-cli INFO keyspace   # should show db0 with keys
```

**RPO:** 1 second (AOF + appendfsync=everysec). **RTO:** 30 seconds (restart + AOF replay for typical keyspace).

---

### Scenario B — Redis data corruption (AOF file corrupted)

**Symptoms:** `redis-check-aof` reports errors; Redis fails to start.

**Recovery steps:**
```bash
# 1. Run AOF repair tool
redis-check-aof --fix /var/lib/redis/appendonly.aof

# 2. If repair fails, truncate to last good position (some recent data lost)
redis-check-aof --fix --yes /var/lib/redis/appendonly.aof

# 3. Restart and verify
systemctl start redis-server
redis-cli PING

# 4. Accept data loss window — dialer will re-queue leads from DB on startup
```

**RPO after corruption:** Varies (worst-case: loses last 1s of writes). **RTO:** 5 minutes.

---

### Scenario C — Failover to replica

**Symptoms:** Primary Redis host unreachable; replica is healthy.

**Recovery steps (replica promotion):**
```bash
# On the replica host
redis-cli REPLICAOF NO ONE   # promotes replica to primary

# Update bff.js + voice runtime Redis URL to point to replica
# Edit /etc/voiceos/cpu.env: REDIS_URL=redis://:PASSWORD@REPLICA_IP:6379/0

# Restart services
systemctl restart voiceos-bff voiceos-voice-runtime voiceos-dialer-worker
```

**RTO:** 30 seconds (including service restart). **Data loss:** Up to 1s (AOF lag).

---

## 3. MongoDB Failure

### Scenario A — mongod process crash

**Symptoms:** `systemctl status mongod` shows failed; voice runtime logs `pymongo.errors.ServerSelectionTimeoutError`.

**Impact:** Decision envelopes, call transcripts, and conversation states cannot be written. The voice path is NOT blocked per-turn (MongoDB writes are async post-turn), but `MongoWriteError` will appear in logs.

**Recovery steps:**
```bash
# 1. Restart mongod
systemctl restart mongod

# 2. Verify replica set primary election (single-node = always primary after restart)
mongosh --eval "rs.status().members[0].stateStr"

# 3. Verify indexes are present
mongosh voiceos --eval "
  ['call_transcripts','decision_envelopes','conversation_states','ml_training_samples']
  .forEach(c => printjson(db[c].getIndexes()));
"
# Re-create any missing indexes from scripts/db/migrations/
```

**RTO:** < 5 minutes. **Data loss:** None (in-memory journal + fsync).

---

### Scenario B — MongoDB data loss / full restore

**Recovery steps (from daily dump):**
```bash
# List available backups
ls /backup/mongodb/

# Restore from specific date (e.g., yesterday)
sudo bash scripts/backup/mongodb_restore.sh 20260916

# Verify counts
mongosh voiceos --eval "db.call_transcripts.countDocuments()"
```

**RPO:** 24 hours (daily dump). **RTO:** 4 hours for full restore. **Data loss window:** Up to 24 hours of voice turn data (non-blocking for recovery calls; PTP/billing data is in Postgres).

---

## 4. Vault Failure

### Scenario A — Vault sealed (auto-unseal failed)

**Symptoms:** `vault status` shows `Sealed: true`; all secrets manager calls fail; services using Vault creds (web_api, encryption) error on startup.

**Recovery steps:**
```bash
# 1. Check seal status
vault status

# 2. Unseal using key from /opt/vault/init.json (SECURITY NOTE: keep this file protected)
UNSEAL_KEY=$(jq -r '.unseal_keys_b64[0]' /opt/vault/init.json)
vault operator unseal "${UNSEAL_KEY}"

# 3. Verify Vault is unsealed
vault status | grep Sealed   # should be "false"

# 4. Restart services that failed during seal window
systemctl restart voiceos-webapi
```

**RTO:** < 2 minutes.

---

### Scenario B — Vault data loss / restore from snapshot

```bash
# 1. Download snapshot from backup
ls /backup/vault/
# Or from MinIO:
mc cp voiceos-backup/voiceos-backups/vault/20260916.snap /tmp/vault.snap

# 2. Stop Vault
systemctl stop vault

# 3. Restore snapshot
vault operator raft snapshot restore /tmp/vault.snap

# 4. Restart Vault and unseal
systemctl start vault
vault operator unseal "$(jq -r '.unseal_keys_b64[0]' /opt/vault/init.json)"

# 5. Verify KV data
vault kv list voiceos/
```

**RPO:** 24 hours. **RTO:** 2 hours. **Impact:** Credentials rotated since last snapshot must be re-rotated.

---

## 5. RTO / RPO Summary

| Store      | RPO     | RTO     | Backup method          | Script |
|------------|---------|---------|------------------------|--------|
| PostgreSQL | 1 hour  | 2 hours | WAL archiving (MinIO)  | `scripts/backup/pg_wal_archiving_setup.sh` |
| Redis      | 1 second| 30 secs | AOF + replica          | `scripts/backup/redis_verify_persistence.sh` |
| MongoDB    | 24 hours| 4 hours | Daily dump (systemd)   | `scripts/backup/mongodb_dump.sh` |
| Vault      | 24 hours| 2 hours | Daily snapshot (systemd)| `scripts/backup/vault_snapshot.sh` |

---

## 6. Contacts and Escalation

- **Primary on-call:** Check `deployment/cpu/GPU_DEPLOYMENT_CHECKLIST.md` for current on-call rotation
- **Vault unseal key location:** `/opt/vault/init.json` on the CPU VM (**R-8 risk** — move to HSM post-launch)
- **Monitoring dashboards:** Prometheus/Grafana at port 3001; Vault at port 8200/ui

---

*Last updated: 2026-09-17 — Phase 11e*
