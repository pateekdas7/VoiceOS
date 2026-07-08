# Sprint-013 — Event Bus & Redis Architecture

**Epic:** E4 — Reliability Infrastructure  
**Status:** ✅ **DONE** (2026-07-04)  
**Depends on:** Sprint-001, Sprint-002, Sprint-003  
**Blocks:** Sprint-015, Sprint-016, Sprint-017  

> **Implementation note (resolved deviations — see DONE.md and CHANGELOG.md for full detail):**
> - This spec's `src/libs/event-bus/` and `src/libs/redis-client/` (hyphenated) are invalid Python package names and inconsistent with every prior sprint's actual directory convention. Implemented as `src/libs/event_bus/` and `src/libs/redis_client/` (underscored) instead — a mechanical necessity, not an architecture change.
> - DLQ stream naming implemented as `dlq:{stream_name}` (matches this doc's own Deployment Procedure / Health Checks / DR Validation sections, used 3×) rather than the single `EVENT_BUS_DLQ=voiceos-events-dlq` occurrence in the env-var table below (internally inconsistent in this spec — the `dlq:` prefix form was treated as authoritative).
> - `RedisClient` implemented as a pooled **synchronous** client (matching the only available test double, `FakeRedisClient`, and the precedent set by `WorkingMemoryStore`), not "async" as this doc's prose states — see DONE.md Deviations for the full rationale.
> - CPU node Redis is 6.0.16, not the "≥6.2" implied by this doc — validated with no functional impact (see CPU_NODE_STATE.md §18).

---

## Objective

Implement the Event Bus — the immutable, ordered, replayable event stream with at-least-once delivery and Dead Letter Queue — and the Redis layer for hot state, distributed locks with fencing tokens, and rate limiting. These are the reliability infrastructure primitives used by every service.

---

## Architecture References

- Volume 3: Ch3 (Event Bus — immutable ordered events, DomainEvent schema, at-least-once + idempotency, DLQ), Ch4 (Redis Architecture — hot state, distributed locks, fencing tokens, TTL discipline, rate limiting, NOT authoritative)
- Volume 6: Ch6 (Event & Message Standards — naming, additive versioning, correlation/causation/trace IDs)
- DocSuite-03: Data Dictionary

---

## Components to Implement

### `src/libs/event-bus/`

```
src/libs/event-bus/
├── __init__.py
├── bus.py                  (EventBus: publish, subscribe, DLQ routing)
├── publisher.py            (Publisher: wraps EventBus.publish(), enforces EventEnvelope schema)
├── consumer.py             (Consumer: subscription management, at-least-once delivery, dedup)
├── dedup.py                (EventDeduplicator: consumer-side dedup by event_id, Redis-backed)
├── dlq.py                  (DLQHandler: dead letter queue — route failed events after N retries)
├── router.py               (EventRouter: event_type → subscriber list mapping)
└── metrics.py              (events_published, events_consumed, dlq_depth, dedup_hits)
```

**EventBus:**
- Backend: Redis Streams (XADD/XREADGROUP) for ordered, persistent, replay-capable stream
- Consumer groups: each service subscribes as a consumer group → parallel consumption
- At-least-once: consumer must ACK (XACK) after processing; unACKed messages are redelivered
- Consumer-side dedup: `EventDeduplicator.is_duplicate(event_id)` checks Redis SET (TTL 24h)
- DLQ: events that fail N retries (default 3) are moved to `dlq:{stream_name}` stream
- Replay: support `replay_from(stream_name, offset)` for crash recovery

**Publisher:**
- Validates EventEnvelope schema before XADD
- Auto-generates `event_id` (UUID v4), `occurred_at`, `trace_id` (from context)
- Enforces EventEnvelope required fields: `event_type`, `version`, `tenant_id`, `payload`

**Consumer:**
- `subscribe(event_type, handler_fn)` — registers handler
- `start()` — begins polling XREADGROUP loop
- Calls `EventDeduplicator.is_duplicate()` before handler; skips if duplicate
- Handles NACK + retry with exponential backoff (1s, 2s, 4s → DLQ)

### `src/libs/redis-client/`

```
src/libs/redis-client/
├── __init__.py
├── client.py               (RedisClient: async Redis connection pool, health check)
├── lock.py                 (DistributedLock: Redlock-style with fencing tokens)
├── rate_limiter.py         (RateLimiter: sliding window, token bucket)
└── ttl_guard.py            (TTLGuard: ensures every SET operation includes TTL — raises if missing)
```

**DistributedLock:**
- Redis SET NX PX (atomic acquire) with configurable TTL (default 30s)
- Fencing token: `lock_version: int` (INCR on each acquire) — prevents split-brain writes
- `acquire(resource_id, owner_id) -> LockToken` (LockToken includes fencing_token: int)
- `release(lock_token)` — Lua script verifies owner before DEL (atomic)
- `assert_ri2_single_writer(resource_id, writer_id, lock_token.fencing_token)` called on every state write

**TTLGuard:**
- Wrapper around `redis.set()` that enforces TTL is always provided
- If `ex` / `px` / `exat` not set → raises `MissingTTLError` (RI-3 compliant; prevents stale key accumulation)
- All Redis writes in the codebase must use `TTLGuard.set()` not `redis.set()` directly

**RateLimiter:**
- Sliding window algorithm (Redis sorted sets)
- Per-tenant + per-user rate limits
- `check(key, limit, window_seconds) -> RateLimitResult(allowed, remaining, retry_after_ms)`

---

## Files Expected to Change

**New:** `src/libs/event-bus/` (all files), `src/libs/redis-client/` (all files)  
**New:** `tests/unit/libs/test_event_bus.py`, `test_redis_client.py`, `test_distributed_lock.py`, `test_rate_limiter.py`  
**New:** `tests/integration/libs/test_event_bus_integration.py`, `test_redis_integration.py`

---

## Acceptance Criteria

- [x] Event published via EventBus → consumed by subscriber in integration test (round-trip)
- [x] Consumer-side dedup: same event_id submitted twice → handler called exactly once
- [x] Events failing N retries are moved to DLQ stream (integration test)
- [x] `TTLGuard.set()` without TTL → raises `MissingTTLError` (unit test)
- [x] `DistributedLock.acquire()` returns LockToken with fencing_token
- [x] `DistributedLock.release()` with wrong owner_id → does NOT release lock (unit test)
- [x] `assert_ri2_single_writer` is called with fencing_token on every lock acquire (coverage test)
- [x] RateLimiter correctly blocks after limit is exceeded, unblocks after window expires

---

## Required Tests

**Unit:**
- `test_event_dedup_same_id` — same event_id twice → second call returns is_duplicate=True
- `test_event_envelope_schema_enforcement` — missing tenant_id → Publisher raises
- `test_ttl_guard_enforces_ttl` — set without TTL → MissingTTLError
- `test_distributed_lock_wrong_owner_release` — release with wrong owner → lock not released
- `test_rate_limiter_blocks_after_limit` — 11 requests in 10s window with limit=10 → 11th blocked

**Integration (real Redis):**
- `test_event_bus_publish_consume` — publish → consume → ACK
- `test_event_bus_dlq_routing` — consumer fails 3 times → event in DLQ stream
- `test_redis_lock_acquire_release` — acquire + work + release cycle

---

## Definition of Done

- [x] All AC items checked
- [x] All tests pass
- [x] TTLGuard enforced on all Redis SET operations (verified by import analysis)
- [x] CI green
- [x] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [x] `CURRENT_SPRINT.md` updated to Sprint-014

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** Redis integration tests use the `FakeRedisClient` fixture for unit tests and `TestRedis` fixture (Docker) for integration tests that run locally.

### Files Created

- `src/libs/event-bus/__init__.py`, `bus.py`, `publisher.py`, `consumer.py`, `dedup.py`, `dlq.py`, `router.py`, `metrics.py`
- `src/libs/redis-client/__init__.py`, `client.py`, `lock.py`, `rate_limiter.py`, `ttl_guard.py`
- `tests/unit/libs/test_event_bus.py`
- `tests/unit/libs/test_redis_client.py`
- `tests/unit/libs/test_distributed_lock.py`
- `tests/unit/libs/test_rate_limiter.py`
- `tests/integration/libs/test_event_bus_integration.py`
- `tests/integration/libs/test_redis_integration.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| Redis (unit tests) | `FakeRedisClient` fixture (Sprint-003) | Injected into EventBus, DistributedLock, RateLimiter |
| Redis (integration tests) | `TestRedis` Docker fixture (Sprint-003) | Real Redis in Docker for XADD/XREADGROUP/Streams |
| Redis Streams | Docker Redis ≥ 6.2 | Real stream commands needed for integration tests |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations; all Redis SET calls via TTLGuard |
| Unit tests | `pytest tests/unit/libs/` | All pass |
| Integration tests | `pytest tests/integration/libs/ -m "not live_redis or docker"` | All pass (Docker Redis) |
| Coverage | `pytest --cov=src --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `TTLGuard.set()` without TTL → `MissingTTLError` raised (RI-3 enforcement)
- `EventDeduplicator`: same `event_id` submitted twice → second returns `is_duplicate=True`
- `DistributedLock.release()` with wrong owner → lock NOT released
- `RateLimiter`: 11th request in 10s window with limit=10 → blocked
- `EventBus` DLQ: consumer fails 3 times → event moved to `dlq:{stream_name}`

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services deployed this sprint:**

| Service | Deployment | Why |
|---|---|---|
| EventBus | Library integrated into ConversationEngine + all services | Redis Streams-backed; first real event bus deployment |
| RedisClient | Shared library; deployed with ConversationEngine | Central async Redis connection pool for all services |
| DistributedLock | Library integrated where needed | Fencing token enforcement activated |
| RateLimiter | Library integrated into API services | Per-tenant sliding window rate limiting active |

**Infrastructure note:** Redis itself (managed Redis or Docker Compose Redis) must be running before this sprint deploys. Redis was introduced in Sprint-010 (WorkingMemory); this sprint wires the full event-streaming layer on top of that same Redis instance.

**Previously deployed services that remain running:**
- Sprint-004–012: All services deployed (Media GW through ConversationEngine)

**Deployment procedure:**
1. Confirm Redis version ≥ 6.2 (Redis Streams support)
2. Deploy EventBus library into ConversationEngine pod (rolling restart)
3. Seed initial consumer groups in Redis: `XGROUP CREATE voiceos-events main-group $ MKSTREAM`
4. Confirm publisher → consumer round-trip: emit `CallStarted` event → consumer logs receipt
5. Confirm DLQ stream created: `XLEN dlq:voiceos-events` = 0 at start

**Health checks:**
- Redis connectivity from all services: `redis-cli PING` → `PONG`
- EventBus consumer group: `XINFO GROUPS voiceos-events` shows active consumer groups
- DLQ depth Prometheus gauge: `eventbus_dlq_depth` = 0 at healthy state
- ConversationEngine: after upgrade, `DecisionEnvelope` published to Redis stream on each call

**Integration validation:**
- Publish `CallStarted` → ConversationEngine subscriber receives within 50ms
- Dedup: publish same event_id twice → consumer processes once (idempotency confirmed in logs)
- DistributedLock: two services attempt to acquire lock on same resource → one acquires, one retries
- RateLimiter: simulate 15 rapid calls for tenant → 5 blocked after limit of 10

**Rollback procedure:**
- EventBus is a library; rollback = redeploy previous ConversationEngine image without EventBus wiring
- Redis streams persist independently — no data loss on rollback

### GPU Node

> **GPU node is not required during this sprint.** Previously deployed GPU services (Whisper, Qwen, Veena) remain running unchanged.

### Infrastructure Validation

**CPU Validation:**
- Redis Streams: `XLEN voiceos-events` grows as calls are processed
- Consumer group lag: `XPENDING voiceos-events main-group - + 10` → 0 pending (all ACKed)
- DLQ depth: `eventbus_dlq_depth` Prometheus gauge = 0 at steady state
- TTLGuard: `redis-cli OBJECT ENCODING <key>` — verify all keys have TTL set (no eternal keys)

**GPU Validation:**
> Not applicable this sprint.

**Networking Validation:**
- All services → Redis: latency < 5ms per XADD/XREADGROUP call
- Event delivery: publish → consume round-trip < 100ms under normal load
- DistributedLock fencing: simulated split-brain — stale writer attempt rejected by Lua script

### Regression Validation

- ConversationEngine: walking skeleton e2e test (`pytest tests/e2e/test_walking_skeleton.py`) — still passes after EventBus wiring
- All Sprint-010–012 services: health endpoints 200
- GPU services: VRAM gauges unchanged
- Redis WorkingMemory TTLs: existing call-state keys retain their TTLs

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [x] EventBus (Publisher, Consumer, DLQ, Dedup) implemented
- [x] RedisClient (DistributedLock with fencing token, RateLimiter, TTLGuard) implemented
- [x] `ruff check`, `ruff format --check`, `mypy --strict`: all pass
- [x] TTLGuard enforced on all Redis SET paths (import analysis)
- [x] Unit + integration tests pass
- [x] Coverage ≥ 85%
- [x] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [x] EventBus deployed and live — events flowing through Redis Streams
- [x] Consumer groups active; zero DLQ depth at steady state
- [x] DistributedLock fencing token enforcement verified
- [x] RateLimiter blocking at configured limits
- [x] Walking skeleton e2e test still passes after EventBus wiring
- [x] Regression tests pass for all prior services
- [x] Deployment remains active as baseline for Sprint-014

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state.

### CPU_NODE_STATE.md — Updates This Sprint

- Update §7.4 Event Bus section: add consumer group creation commands, DLQ stream name, TTLGuard enforcement note
- Add `EventBusService`, `DistributedLockService`, `RateLimiterService` to Services table (§8.1)
- Update health check commands: add EventBus health check (`XINFO GROUPS voiceos-events`) (§14)
- Add environment variables: `EVENT_BUS_STREAM=voiceos-events`, `EVENT_BUS_DLQ=voiceos-events-dlq`, `EVENT_BUS_CONSUMER_GROUP=main-group` (§11)
- Update Port Map (§9.1)

### GPU_NODE_STATE.md — Updates This Sprint

GPU node unchanged. `GPU_NODE_STATE.md` last_updated remains: Sprint-012.

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/healthcheck.sh` | Add EventBus health check: `redis-cli XINFO GROUPS voiceos-events` |
| `deployment/cpu/.env.example` | Add EventBus variable descriptions |
| `deployment/cpu/restore.sh` | Add EventBus consumer group creation step after Redis is ready |

### DR Validation

**CPU node rebuild test:**
```bash
sudo bash deployment/cpu/bootstrap.sh
bash deployment/cpu/restore.sh
bash deployment/cpu/healthcheck.sh
# Expected: EventBus consumer group 'main-group' exists; DLQ depth = 0
```

**EventBus-specific validation:**
```bash
redis-cli -h $REDIS_HOST -a $REDIS_PASSWORD XINFO GROUPS voiceos-events
# Expected: main-group consumer group present
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
# Expected: walking skeleton e2e test passes with EventBus wired in
```
