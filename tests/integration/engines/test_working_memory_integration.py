"""Integration tests for WorkingMemoryStore against a real Redis instance.

Required named test (per Sprint-010 spec):
  - test_working_memory_redis_roundtrip

Skipped when REDIS_URL is not set (CI/local with no Redis).
Architecture: V2 Ch11; DocSuite-08.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.engines.memory.working.schema import WorkingMemoryDelta
from src.engines.memory.working.store import WorkingMemoryStore
from src.libs.contracts.response_plan import IntentLabel
from tests.integration.conftest import requires_redis

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def redis_client() -> Any:
    """Real Redis client using TestRedis on db=15 (test isolation)."""
    import redis as redis_lib

    from tests.fixtures.redis import REDIS_URL

    # protocol=2: broad compatibility with Redis versions predating the RESP3
    # HELLO handshake (Redis < 6) — see tests/fixtures/redis.py TestRedis.
    client = redis_lib.Redis.from_url(REDIS_URL, db=15, decode_responses=False, protocol=2)
    yield client
    client.flushdb()
    client.close()


@pytest.fixture
def store(redis_client: Any) -> WorkingMemoryStore:
    return WorkingMemoryStore(redis=redis_client)


# ---------------------------------------------------------------------------
# Required named integration test
# ---------------------------------------------------------------------------


@requires_redis
def test_working_memory_redis_roundtrip(store: WorkingMemoryStore, redis_client: Any) -> None:
    """AC-5: WorkingMemory round-trips correctly through real Redis.

    Sets call state, reads it back, verifies all fields match.
    """
    call_id = "integration-call-001"

    store.update(
        call_id,
        WorkingMemoryDelta(
            turn_count=3,
            last_intent=IntentLabel.PAYMENT,
            extracted_entities={"AMOUNT": "5000"},
            negotiation_state="offer_made",
            last_strategy="NEGOTIATE",
            agent_utterances=("Namaste", "Aapka outstanding..."),
            customer_utterances=("haan", "main sochta hun"),
        ),
    )

    mem = store.get(call_id)
    assert mem.call_id == call_id
    assert mem.turn_count == 3
    assert mem.last_intent == IntentLabel.PAYMENT
    assert mem.extracted_entities == {"AMOUNT": "5000"}
    assert mem.negotiation_state == "offer_made"
    assert mem.last_strategy == "NEGOTIATE"
    assert mem.agent_utterances == ("Namaste", "Aapka outstanding...")
    assert mem.customer_utterances == ("haan", "main sochta hun")

    key = f"wm:{call_id}"
    ttl = redis_client.ttl(key)
    assert ttl > 0, f"Expected positive TTL, got {ttl}"
    assert ttl <= WorkingMemoryStore.WORKING_MEMORY_TTL


@requires_redis
def test_working_memory_clear_removes_key(store: WorkingMemoryStore, redis_client: Any) -> None:
    """clear() removes the Redis key (integration verification)."""
    call_id = "integration-clear-001"
    store.update(call_id, WorkingMemoryDelta(turn_count=1))

    key = f"wm:{call_id}"
    assert redis_client.exists(key) == 1

    store.clear(call_id)
    assert redis_client.exists(key) == 0


@requires_redis
def test_working_memory_ttl_is_4_hours(store: WorkingMemoryStore, redis_client: Any) -> None:
    """TTL set on Redis key must be within [14390, 14400] seconds."""
    call_id = "integration-ttl-001"
    store.update(call_id, WorkingMemoryDelta(turn_count=1))

    key = f"wm:{call_id}"
    ttl = redis_client.ttl(key)
    assert 14390 <= ttl <= 14400, f"TTL {ttl} not in expected range [14390, 14400]"


@requires_redis
def test_working_memory_merge_semantics(store: WorkingMemoryStore) -> None:
    """Multiple updates merge correctly (later updates don't erase earlier fields)."""
    call_id = "integration-merge-001"
    store.update(call_id, WorkingMemoryDelta(turn_count=1, last_intent=IntentLabel.PROMISE_TO_PAY))
    store.update(call_id, WorkingMemoryDelta(negotiation_state="ptp_confirmed"))

    mem = store.get(call_id)
    assert mem.turn_count == 1
    assert mem.last_intent == IntentLabel.PROMISE_TO_PAY
    assert mem.negotiation_state == "ptp_confirmed"
