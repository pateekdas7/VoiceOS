"""Unit tests for WorkingMemoryStore using FakeRedisClient.

Required named tests (per Sprint-010 spec):
  - test_working_memory_ttl

Architecture: V2 Ch11; DocSuite-08.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from src.engines.memory.working.schema import WorkingMemory, WorkingMemoryDelta
from src.engines.memory.working.store import WorkingMemoryStore
from src.libs.contracts.response_plan import IntentLabel
from tests.fixtures.redis import FakeRedisClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def redis() -> Iterator[FakeRedisClient]:
    client = FakeRedisClient()
    yield client
    client.flushdb()


@pytest.fixture
def store(redis: FakeRedisClient) -> WorkingMemoryStore:
    return WorkingMemoryStore(redis=redis)


# ---------------------------------------------------------------------------
# Required named tests
# ---------------------------------------------------------------------------


def test_working_memory_ttl(store: WorkingMemoryStore, redis: FakeRedisClient) -> None:
    """AC-5: Redis key has 4-hour TTL set after update."""
    call_id = "call-ttl-001"
    delta = WorkingMemoryDelta(turn_count=1)
    store.update(call_id, delta)

    key = f"wm:{call_id}"
    ttl = redis._ttls.get(key)
    assert ttl == WorkingMemoryStore.WORKING_MEMORY_TTL, (
        f"Expected TTL={WorkingMemoryStore.WORKING_MEMORY_TTL}, got {ttl}"
    )


# ---------------------------------------------------------------------------
# Core round-trip tests
# ---------------------------------------------------------------------------


def test_get_returns_empty_for_new_call(store: WorkingMemoryStore) -> None:
    """Getting memory for unknown call_id returns empty WorkingMemory."""
    mem = store.get("call-new-001")
    assert isinstance(mem, WorkingMemory)
    assert mem.call_id == "call-new-001"
    assert mem.turn_count == 0
    assert mem.last_intent is None


def test_update_turn_count(store: WorkingMemoryStore) -> None:
    """update() with turn_count delta changes the stored value."""
    call_id = "call-upd-001"
    store.update(call_id, WorkingMemoryDelta(turn_count=1))
    mem = store.get(call_id)
    assert mem.turn_count == 1


def test_update_increments_turn_count(store: WorkingMemoryStore) -> None:
    """Two updates accumulate correctly."""
    call_id = "call-inc-001"
    store.update(call_id, WorkingMemoryDelta(turn_count=1))
    store.update(call_id, WorkingMemoryDelta(turn_count=2))
    mem = store.get(call_id)
    assert mem.turn_count == 2


def test_update_last_intent(store: WorkingMemoryStore) -> None:
    """update() persists last_intent."""
    call_id = "call-intent-001"
    store.update(call_id, WorkingMemoryDelta(last_intent=IntentLabel.PAYMENT))
    mem = store.get(call_id)
    assert mem.last_intent == IntentLabel.PAYMENT


def test_update_extracted_entities(store: WorkingMemoryStore) -> None:
    """update() persists extracted_entities."""
    call_id = "call-ent-001"
    entities = {"AMOUNT": "5000", "PROMISE_DATE": "2026-07-04"}
    store.update(call_id, WorkingMemoryDelta(extracted_entities=entities))
    mem = store.get(call_id)
    assert mem.extracted_entities == entities


def test_update_negotiation_state(store: WorkingMemoryStore) -> None:
    """update() persists negotiation_state."""
    call_id = "call-neg-001"
    store.update(call_id, WorkingMemoryDelta(negotiation_state="offer_made"))
    mem = store.get(call_id)
    assert mem.negotiation_state == "offer_made"


def test_update_agent_utterances(store: WorkingMemoryStore) -> None:
    """update() persists agent_utterances tuple."""
    call_id = "call-agt-001"
    utterances: tuple[str, ...] = ("Namaste", "Aapka account...")
    store.update(call_id, WorkingMemoryDelta(agent_utterances=utterances))
    mem = store.get(call_id)
    assert mem.agent_utterances == utterances


def test_update_customer_utterances(store: WorkingMemoryStore) -> None:
    """update() persists customer_utterances tuple."""
    call_id = "call-cust-001"
    utterances: tuple[str, ...] = ("haan", "main de dunga")
    store.update(call_id, WorkingMemoryDelta(customer_utterances=utterances))
    mem = store.get(call_id)
    assert mem.customer_utterances == utterances


def test_clear_removes_entry(store: WorkingMemoryStore) -> None:
    """clear() removes the Redis key; subsequent get returns empty."""
    call_id = "call-clear-001"
    store.update(call_id, WorkingMemoryDelta(turn_count=5))
    store.clear(call_id)
    mem = store.get(call_id)
    assert mem.turn_count == 0
    assert mem.last_intent is None


def test_clear_nonexistent_is_safe(store: WorkingMemoryStore) -> None:
    """clear() on a non-existent key does not raise."""
    store.clear("call-nonexistent-001")  # must not raise


def test_delta_none_fields_unchanged(store: WorkingMemoryStore) -> None:
    """None fields in WorkingMemoryDelta are not applied."""
    call_id = "call-merge-001"
    store.update(call_id, WorkingMemoryDelta(turn_count=3, last_intent=IntentLabel.DISPUTE))
    # Update only negotiation_state, leave turn_count and last_intent unchanged
    store.update(call_id, WorkingMemoryDelta(negotiation_state="dispute_raised"))
    mem = store.get(call_id)
    assert mem.turn_count == 3
    assert mem.last_intent == IntentLabel.DISPUTE
    assert mem.negotiation_state == "dispute_raised"


def test_ttl_constant_is_4_hours() -> None:
    """WORKING_MEMORY_TTL must be 14400 seconds (4 hours)."""
    assert WorkingMemoryStore.WORKING_MEMORY_TTL == 14400


def test_multiple_calls_isolated(store: WorkingMemoryStore) -> None:
    """Separate call_ids maintain isolated working memory."""
    store.update("call-A", WorkingMemoryDelta(turn_count=1, negotiation_state="A"))
    store.update("call-B", WorkingMemoryDelta(turn_count=99, negotiation_state="B"))
    mem_a = store.get("call-A")
    mem_b = store.get("call-B")
    assert mem_a.turn_count == 1
    assert mem_b.turn_count == 99
    assert mem_a.negotiation_state == "A"
    assert mem_b.negotiation_state == "B"


def test_working_memory_is_frozen() -> None:
    """WorkingMemory is a frozen Pydantic model (immutable)."""
    from pydantic import ValidationError

    mem = WorkingMemory(call_id="call-frozen")
    with pytest.raises(ValidationError):
        mem.turn_count = 99  # type: ignore[misc]


def test_working_memory_default_state() -> None:
    """Default WorkingMemory has expected initial values."""
    mem = WorkingMemory(call_id="call-defaults")
    assert mem.turn_count == 0
    assert mem.last_intent is None
    assert mem.extracted_entities == {}
    assert mem.negotiation_state == "initial"
    assert mem.last_strategy is None
    assert mem.agent_utterances == ()
    assert mem.customer_utterances == ()


def test_update_last_strategy(store: WorkingMemoryStore) -> None:
    """update() persists last_strategy."""
    call_id = "call-strat-001"
    store.update(call_id, WorkingMemoryDelta(last_strategy="NEGOTIATE"))
    mem = store.get(call_id)
    assert mem.last_strategy == "NEGOTIATE"
