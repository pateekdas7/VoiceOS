# Sprint-015 — State Persistence, Crash Recovery & Idempotency

**Epic:** E4 — Reliability Infrastructure  
**Status:** ⬜ Pending  
**Depends on:** Sprint-013, Sprint-014  
**Blocks:** Sprint-016  

---

## Objective

Implement snapshot-based state persistence with event-tail replay (for durable conversation state), the deterministic per-failure-class crash recovery protocol, and IdempotencyGuard.execute_once() for all authoritative effects. After this sprint, no committed event is ever lost and no authoritative effect is ever duplicated.

---

## Architecture References

- Volume 3: Ch6 (State Persistence — Snapshot + event tail replay, Recoverable protocol), Ch7 (Crash Recovery — deterministic recovery per failure class), Ch8 (Idempotency — IdempotencyGuard.execute_once(), fencing tokens, consumer dedup)
- DocSuite-03: Data Dictionary

---

## Components to Implement

### `src/libs/state/`

```
src/libs/state/
├── __init__.py
├── snapshot.py             (Snapshot: periodic state serialization to Postgres)
├── replay.py               (EventTailReplay: replay events from snapshot forward)
├── recoverable.py          (Recoverable: mixin protocol for any stateful component)
└── recovery_log.py         (RecoveryLog: records recovery attempts and outcomes)
```

**Recoverable protocol:**
```python
class Recoverable(Protocol):
    def snapshot(self) -> StateSnapshot: ...
    def restore(self, snapshot: StateSnapshot) -> None: ...
    def apply_event(self, event: DomainEvent) -> None: ...
```

**Snapshot:**
- `take_snapshot(component: Recoverable, call_id: str) -> None` — serializes state to Postgres `snapshots` table
- `load_latest_snapshot(call_id: str) -> StateSnapshot | None`
- Periodic: every 60 seconds during active call, or after every 10 events

**EventTailReplay:**
- `replay_from_snapshot(call_id: str, component: Recoverable) -> int` — loads snapshot, replays event tail from Redis Streams
- Returns count of events replayed
- Events replayed in order (by offset) → deterministic final state

### Crash Recovery Matrix (`src/libs/recovery/`)

```
src/libs/recovery/
├── __init__.py
├── recovery_manager.py     (RecoveryManager: detects failure class, dispatches recovery)
├── strategies/
│   ├── cpu_restart.py      (CPURestartStrategy: replay from last snapshot)
│   ├── gpu_failure.py      (GPUFailureStrategy: graceful TTS fade + re-queue on surviving GPU)
│   ├── redis_outage.py     (RedisOutageStrategy: degrade to Postgres, no new Redis state)
│   ├── db_outage.py        (DBOutageStrategy: halt new calls, hold existing in-memory)
│   ├── twilio_disconnect.py (TwilioDisconnectStrategy: 30s reconnect attempt then close session)
│   └── network_partition.py (NetworkPartitionStrategy: island mode, drain calls, await reconnect)
```

**RecoveryManager:**
- Subscribes to health events (GPU failure, Redis unavailable, DB connection lost, etc.)
- Maps failure event → recovery strategy
- Executes strategy in order; if strategy fails → escalate to CRITICAL alert + drain
- Emits `RecoveryStarted`, `RecoveryCompleted` domain events

### `src/libs/idempotency/`

```
src/libs/idempotency/
├── __init__.py
├── guard.py                (IdempotencyGuard: execute_once())
├── key_builder.py          (IdempotencyKeyBuilder: deterministic key from call context)
└── fencing.py              (FencingToken: validates token sequence before write)
```

**IdempotencyGuard.execute_once():**
```python
async def execute_once(
    key: str, 
    effect_fn: Callable[[], Awaitable[T]],
    ttl_seconds: int = 3600
) -> T:
    """
    Atomic: check idempotency_keys table for key.
    If found: return cached result.
    If not found: execute effect_fn(), store result, return result.
    Entire check+execute+store is wrapped in Postgres transaction.
    """
```

**FencingToken:**
- Before any authoritative write: `fencing.validate(lock_token.fencing_token, resource_id)`
- Checks that the fencing_token in the lock is ≥ the last seen token for this resource
- Rejects stale writes (where a delayed writer tries to write after a newer lock was acquired)

---

## Files Expected to Change

**New:** `src/libs/state/`, `src/libs/recovery/`, `src/libs/idempotency/`  
**New:** Migration for `snapshots` table and `recovery_log` table  
**New:** `tests/unit/libs/test_idempotency.py`, `test_state_persistence.py`, `test_crash_recovery.py`  
**New:** `tests/integration/libs/test_recovery_integration.py`

---

## Acceptance Criteria

- [ ] `IdempotencyGuard.execute_once()` with same key twice → second call returns first call's result, effect_fn not called again
- [ ] `EventTailReplay.replay_from_snapshot()` correctly replays all events since snapshot → component state matches expected
- [ ] CPU restart recovery: simulate crash → restart → replay → state is identical to pre-crash state
- [ ] GPU failure recovery: simulate GPU failure → ConversationEngine gracefully halts current TTS, logs recovery, re-queues on surviving GPU
- [ ] Fencing token validation: stale write (lower fencing token) → rejected
- [ ] `RecoveryManager` correctly dispatches to the right strategy for each failure class

---

## Required Tests

**Unit:**
- `test_idempotency_second_call_returns_cached` — same key → effect_fn called once, result returned twice
- `test_fencing_token_rejects_stale` — token 5 after token 8 seen → rejected
- `test_snapshot_roundtrip` — take snapshot → restore from snapshot → state identical
- `test_replay_restores_state` — snapshot + 5 events → replay → correct final state
- `test_cpu_restart_recovery` — CPURestartStrategy.recover() → state matches pre-crash

**Integration:**
- `test_idempotency_concurrent_requests` — 10 concurrent calls with same key → effect_fn called exactly once (Postgres transaction test)
- `test_replay_from_postgres` — real Postgres: save snapshot, write events, replay → correct state

---

## Definition of Done

- [ ] All AC items checked
- [ ] All tests pass (including concurrent idempotency test)
- [ ] CI green
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-016

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** State persistence and idempotency tests use `TestPostgres` and `TestRedis` Docker fixtures locally.

### Files Created

- `src/libs/state/__init__.py`, `snapshot.py`, `replay.py`, `recoverable.py`, `recovery_log.py`
- `src/libs/recovery/__init__.py`, `recovery_manager.py`
- `src/libs/recovery/strategies/cpu_restart.py`, `gpu_failure.py`, `redis_outage.py`, `db_outage.py`, `twilio_disconnect.py`, `network_partition.py`
- `src/libs/idempotency/__init__.py`, `guard.py`, `key_builder.py`, `fencing.py`
- Alembic migrations for `snapshots` table and `recovery_log` table
- `tests/unit/libs/test_idempotency.py`, `test_state_persistence.py`, `test_crash_recovery.py`
- `tests/integration/libs/test_recovery_integration.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| Postgres (snapshots) | `TestPostgres` Docker fixture | Real snapshot save/load from test DB |
| Redis (event tail replay) | `TestRedis` Docker fixture | Real Redis Streams for replay test |
| ConversationEngine (crash simulation) | `FakeRecoverable` test double | Implements `Recoverable` protocol with controllable state |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations |
| Unit tests | `pytest tests/unit/libs/test_idempotency.py tests/unit/libs/test_state_persistence.py tests/unit/libs/test_crash_recovery.py` | All pass |
| Concurrent idempotency | `pytest tests/integration/libs/test_recovery_integration.py::test_idempotency_concurrent_requests` | effect_fn called exactly once |
| Coverage | `pytest --cov=src --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `IdempotencyGuard.execute_once(key, fn)`: same key twice → `fn` called once, cached result returned twice
- `FencingToken.validate(token=5, last_seen=8)` → rejected (stale write)
- `Snapshot.take() → restore()` → state identical
- `EventTailReplay`: snapshot + 5 events → correct final state
- `CPURestartStrategy.recover()` → state matches pre-crash via replay

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services integrated/upgraded this sprint:**

| Action | Service | Why |
|---|---|---|
| Integrate IdempotencyGuard | ConversationEngine, all PTP/consent mutation paths | Guarantees exactly-once effects on authoritative writes |
| Integrate Snapshot + EventTailReplay | ConversationEngine | Enables crash recovery with no data loss |
| Deploy RecoveryManager | ConversationEngine sidecar or inline | Handles per-failure-class recovery automatically |
| Add `snapshots` table migration | Postgres | Persistent state snapshot storage |
| Add `recovery_log` table migration | Postgres | Auditable recovery attempt history |

**Previously deployed services that remain running:**
- All Sprint-004–014 services

**Deployment procedure:**
1. Run new Alembic migrations (`snapshots`, `recovery_log` tables): `alembic upgrade head`
2. Rolling restart of ConversationEngine with IdempotencyGuard and snapshot wiring
3. Simulate controlled crash: kill ConversationEngine pod during active call → verify RecoveryManager triggers
4. Confirm snapshot taken at startup: `SELECT COUNT(*) FROM snapshots WHERE created_at > NOW() - INTERVAL '5 minutes'` → ≥ 1

**Health checks:**
- ConversationEngine: `GET /health/ready` → 200 after restart (state recovered)
- `recovery_log` table: check last entry shows recovery success, not failure
- IdempotencyGuard: submit same `call_id + action` twice via API → one DB record created

**Crash recovery drill:**
1. Start call, process 3 turns (3 snapshots taken)
2. Kill ConversationEngine pod
3. Kubernetes restarts pod → RecoveryManager loads latest snapshot
4. Replay events from Redis Streams since snapshot
5. Verify: 4th turn processed correctly using recovered state

**Rollback procedure:**
- IdempotencyGuard is a library — rollback = redeploy ConversationEngine without IdempotencyGuard wiring
- Snapshot/recovery tables: do not drop (they persist for future recovery; migration is additive)

### GPU Node

> **GPU node is not required during this sprint.** Previously deployed GPU services remain running unchanged.

### Infrastructure Validation

**CPU Validation:**
- Crash recovery drill: ConversationEngine restarts → recovers state within 10s → processes next turn correctly
- Idempotency: 10 concurrent POST requests to create same PTP → exactly 1 DB record created
- Snapshot schedule: Postgres `snapshots` table has entries with timestamps ~60s apart during active calls
- Fencing token: two concurrent lock acquires → one succeeds; stale writer attempt → rejected

**GPU Validation:**
> Not applicable this sprint.

**Networking Validation:**
- ConversationEngine → Postgres (snapshot writes): latency < 30ms
- ConversationEngine → Redis (event tail replay): latency < 10ms per event

### Regression Validation

- Walking skeleton e2e test: `pytest tests/e2e/test_walking_skeleton.py` — still passes after IdempotencyGuard wiring
- GPU Scheduler: `pytest tests/integration/services/test_gpu_scheduler_integration.py`
- EventBus: `pytest tests/integration/libs/test_event_bus_integration.py`
- All prior health endpoints: 200

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] IdempotencyGuard, Snapshot, EventTailReplay, RecoveryManager implemented
- [ ] All 6 recovery strategies implemented
- [ ] `ruff check`, `ruff format --check`, `mypy --strict`: all pass
- [ ] Concurrent idempotency test passes (exactly 1 effect on 10 concurrent calls)
- [ ] Crash recovery unit test passes
- [ ] Coverage ≥ 85%
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] Postgres migrations for `snapshots` and `recovery_log` applied
- [ ] Crash recovery drill passed (restart → recover → continue call)
- [ ] Idempotency verified on production Postgres (10 concurrent calls → 1 record)
- [ ] RecoveryManager dispatches correct strategy for each failure class
- [ ] All regression tests pass
- [ ] Deployment remains active as baseline for Sprint-016

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state.

### CPU_NODE_STATE.md — Updates This Sprint

- Add `RecoveryManager`, `SnapshotManager`, `EventReplayEngine`, `IdempotencyGuard` to Services table (§8.1) — note these are library packages embedded in ConversationEngine, not standalone services
- Update §8.3 Startup Order: note that ConversationEngine now auto-recovers session state from Redis snapshot on startup
- Add to §14 Health Checks: crash recovery verification command (kill pod → restart → verify call continues)
- Update §10 Volumes: note Redis snapshot persistence path (ConversationEngine snapshot keys with TTL)

### GPU_NODE_STATE.md — Updates This Sprint

GPU node unchanged. `GPU_NODE_STATE.md` last_updated remains: Sprint-012.

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/restore.sh` | Note: RecoveryManager auto-triggers on ConversationEngine startup if stale sessions found |

### DR Validation

**Crash recovery validation (critical for cloud node termination):**
```bash
# Simulate cloud node termination: kill ConversationEngine mid-call
kubectl delete pod <conversation-engine-pod> -n voiceos-runtime

# Kubernetes auto-restarts pod; verify recovery
kubectl wait --for=condition=ready pod -l app=conversation-engine -n voiceos-runtime --timeout=60s

# Verify RecoveryManager loaded snapshot and resumed
kubectl logs -l app=conversation-engine -n voiceos-runtime --tail=50 | grep "RecoveryManager"
# Expected: "Session recovered from snapshot" log entry
```

**Idempotency after rebuild:**
```bash
# 10 concurrent POST requests with same idempotency key
python3 scripts/validate/idempotency_test.py --concurrent 10
# Expected: exactly 1 record in Postgres
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
# Expected: all tests pass; crash recovery drill succeeds
```
