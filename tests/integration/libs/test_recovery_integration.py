"""Integration tests: crash recovery against real Postgres + Redis (Sprint-015).

Required named tests:
  - test_idempotency_concurrent_requests — 10 concurrent calls, same key,
    real Postgres, real OS threads (each with its own connection) -> effect_fn
    called exactly once.
  - test_replay_from_postgres — real Postgres snapshot + real Redis Streams
    event tail -> replay -> correct final state.

Skipped when POSTGRES_DSN / REDIS_URL are not set.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from src.libs.contracts.primitives import CallId, TenantId
from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.publisher import Publisher
from src.libs.idempotency.guard import IdempotencyGuard
from src.libs.repositories.idempotency import IdempotencyRepository
from src.libs.state.replay import EventTailReplay
from src.libs.state.snapshot import Snapshot
from tests.fixtures.db import POSTGRES_DSN
from tests.fixtures.recoverable import FakeRecoverable
from tests.fixtures.redis import TestRedis
from tests.integration.conftest import requires_postgres, requires_redis


@requires_postgres
def test_idempotency_concurrent_requests() -> None:
    """10 concurrent callers, same key, real Postgres -> effect_fn runs exactly once."""
    import asyncio

    import psycopg2

    tenant_id = TenantId(str(uuid.uuid4()))
    key = f"key-{uuid.uuid4()}"

    call_count = 0
    count_lock = threading.Lock()

    def run_once(_index: int) -> dict[str, int]:
        conn = psycopg2.connect(POSTGRES_DSN)
        try:
            repo = IdempotencyRepository(conn)
            guard = IdempotencyGuard(repo, poll_interval_seconds=0.02, poll_timeout_seconds=10.0)

            async def effect_fn() -> dict[str, int]:
                nonlocal call_count
                with count_lock:
                    call_count += 1
                return {"n": 1}

            return asyncio.run(guard.execute_once(tenant_id, key, "ptp", effect_fn))
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(run_once, range(10)))

    assert call_count == 1
    assert all(result == {"n": 1} for result in results)

    cleanup_conn = psycopg2.connect(POSTGRES_DSN)
    try:
        cur = cleanup_conn.cursor()
        cur.execute("DELETE FROM idempotency_keys WHERE key = %s", (key,))
        cleanup_conn.commit()
    finally:
        cleanup_conn.close()


@requires_postgres
@requires_redis
def test_replay_from_postgres(pg_conn: Any) -> None:
    """Real Postgres snapshot + real Redis Streams event tail -> replay -> correct state."""
    tenant_id = TenantId(str(uuid.uuid4()))
    call_id = CallId(f"call-{uuid.uuid4()}")

    pre_crash = FakeRecoverable(call_id, counter=3)
    snapshot_store = Snapshot(pg_conn)
    snapshot_store.take_snapshot(pre_crash, tenant_id, call_id)

    stream = f"voiceos:test:sprint015:{uuid.uuid4().hex[:8]}"
    with TestRedis() as r:
        bus = EventBus(r.client, stream=stream)
        publisher = Publisher(bus)
        for _ in range(4):
            publisher.publish(
                event_type="decision.made",
                tenant_id=tenant_id,
                payload={"call_id": call_id},
                correlation_id="corr-1",
            )

        loaded_snapshot = snapshot_store.load_latest_snapshot(tenant_id, call_id)
        assert loaded_snapshot is not None
        assert loaded_snapshot.state == {"counter": 3}

        replay = EventTailReplay(bus)
        restarted = FakeRecoverable(call_id)
        events_replayed = replay.replay_from_snapshot(call_id, restarted, loaded_snapshot)

        assert events_replayed == 4
        assert restarted.counter == 3 + 4

        r.client.delete(stream)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
