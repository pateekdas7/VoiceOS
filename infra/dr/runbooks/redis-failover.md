# Runbook: Redis Failover

**RTO target:** ≤ 30 min.
**Data-loss expectation:** zero loss for TTL-managed keys backed by AOF (`appendfsync everysec`, TT-002, resolved Sprint-013/hardened pre-Sprint-019) — worst case is up to 1 second of the most recent writes, per the documented `appendfsync everysec` trade-off.

## Scope

Today's topology: native Redis process on the CPU node (`deployment/CPU_NODE_STATE.md` §7.1), AOF + RDB persistence hardened per TT-002. Target managed topology: ElastiCache/Memorystore (`infra/terraform/modules/redis`), Multi-AZ configurable.

## Detection

1. `deployment/cpu/healthcheck.sh`'s Redis check fails, or `NodeDown`/`ErrorRateSpike` Prometheus alerts fire referencing `redis`.
2. Confirm directly:
   ```bash
   redis-cli -a "$REDIS_PASSWORD" ping
   ```

## Procedure — single-node topology (current)

1. Restart the native process (the common case — a crash, not disk loss):
   ```bash
   systemctl restart redis-server
   ```
2. Verify AOF replay completed cleanly (Redis logs `DB loaded from append only file`), and that the EventBus consumer group survived (`scripts/eventbus_recovery.py`'s self-heal handles the case where it did not):
   ```bash
   python3 scripts/eventbus_recovery.py
   bash infra/dr/scripts/verify-recovery.sh --component redis
   ```
3. If the data directory itself is lost (disk failure, not just a process crash): restore `appendonly.aof` + `dump.rdb` from the most recent backup, then start Redis and repeat step 2.

## Procedure — target managed topology (ElastiCache/Memorystore, once provisioned)

1. Managed failover to the configured Multi-AZ replica is automatic.
2. Application reconnects via the stable cluster endpoint (never a node IP) — same precedent as the Postgres runbook.
3. Verify: `bash infra/dr/scripts/verify-recovery.sh --component redis`.

## Verifying zero data loss for TTL-managed keys

Redis is explicitly **not authoritative state** (`src/libs/redis_client`'s own invariant, enforced by tests) — every key carries a mandatory TTL (`TTLGuard`, Sprint-013). A failover that loses the last <1s of writes therefore never loses authoritative data (that lives in Postgres); it can only cause a distributed lock/rate-limiter/working-memory entry to be re-derived slightly early. `verify-recovery.sh --component redis` checks:

1. `EventBus` consumer group (`main-group`) is present and `pending=0` after recovery.
2. A round-trip publish→consume succeeds.
3. `TTLGuard`-managed keys still carry a TTL (never `-1`, i.e. never accidentally made permanent by a bad restore).

## Rollback

If the recovered instance fails verification, do not resume traffic. Fall back to the last full AOF+RDB snapshot backup and repeat.

## Post-incident

Record actual recovery time in `infra/dr/DR_DRILL_REPORT_<date>.md`.
