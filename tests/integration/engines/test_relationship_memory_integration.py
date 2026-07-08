"""Integration tests for RelationshipMemoryStore against real PostgreSQL.

Required named test (per Sprint-010 spec):
  - test_relationship_memory_postgres_roundtrip

Skipped when POSTGRES_DSN is not set.
Architecture: V2 Ch12; DocSuite-08.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.engines.memory.relationship.schema import (
    CallSummary,
    PromiseRecord,
    RelationshipMemory,
)
from src.engines.memory.relationship.store import RelationshipMemoryStore
from tests.integration.conftest import requires_postgres

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pg_conn() -> Any:
    """Real Postgres connection on the test database."""
    import psycopg2

    from tests.fixtures.db import POSTGRES_DSN

    conn = psycopg2.connect(POSTGRES_DSN)
    yield conn

    cur = conn.cursor()
    cur.execute("DELETE FROM relationship_memory WHERE customer_id LIKE 'integ-%'")
    conn.commit()
    conn.close()


@pytest.fixture
def store(pg_conn: Any) -> RelationshipMemoryStore:
    return RelationshipMemoryStore(conn=pg_conn)


# ---------------------------------------------------------------------------
# Required named integration test
# ---------------------------------------------------------------------------


@requires_postgres
def test_relationship_memory_postgres_roundtrip(store: RelationshipMemoryStore) -> None:
    """AC-6: RelationshipMemory round-trips correctly through real Postgres."""
    customer_id = "integ-cust-001"
    ptp = PromiseRecord(
        call_id="integ-call-001",
        promised_date="2026-07-10",
        amount_minor=500000,
        fulfilled=False,
    )
    summary = CallSummary(
        call_id="integ-call-001",
        sentiment="positive",
        outcome="ptp_made",
        escalated=False,
        ptp=ptp,
        contact_time_label="morning",
        language="hi-IN",
    )

    store.update(customer_id, summary)

    mem = store.get(customer_id)
    assert isinstance(mem, RelationshipMemory)
    assert mem.customer_id == customer_id
    assert mem.total_calls == 1
    assert len(mem.ptp_history) == 1
    assert mem.ptp_history[0].promised_date == "2026-07-10"
    assert mem.ptp_history[0].amount_minor == 500000
    assert "positive" in mem.sentiment_history
    assert mem.last_call_outcome == "ptp_made"
    assert mem.best_contact_time == "morning"
    assert mem.preferred_language == "hi-IN"
    assert mem.escalation_count == 0


@requires_postgres
def test_relationship_memory_get_unknown_returns_empty(store: RelationshipMemoryStore) -> None:
    """get() on an unknown customer returns empty RelationshipMemory."""
    mem = store.get("integ-unknown-xyz")
    assert isinstance(mem, RelationshipMemory)
    assert mem.total_calls == 0
    assert mem.ptp_history == ()
    assert mem.escalation_count == 0


@requires_postgres
def test_relationship_memory_two_updates(store: RelationshipMemoryStore) -> None:
    """Two updates accumulate total_calls and sentiment_history correctly."""
    customer_id = "integ-cust-002"
    store.update(customer_id, CallSummary(call_id="c1", sentiment="neutral", outcome="callback"))
    store.update(customer_id, CallSummary(call_id="c2", sentiment="positive", outcome="ptp_made"))

    mem = store.get(customer_id)
    assert mem.total_calls == 2
    assert "neutral" in mem.sentiment_history
    assert "positive" in mem.sentiment_history


@requires_postgres
def test_relationship_memory_escalation_count(store: RelationshipMemoryStore) -> None:
    """Escalated calls increment escalation_count correctly."""
    customer_id = "integ-cust-003"
    store.update(customer_id, CallSummary(call_id="c1", sentiment="hostile", outcome="escalated", escalated=True))
    store.update(customer_id, CallSummary(call_id="c2", sentiment="neutral", outcome="callback", escalated=False))

    mem = store.get(customer_id)
    assert mem.escalation_count == 1


@requires_postgres
def test_relationship_memory_table_created_idempotently(pg_conn: Any) -> None:
    """RelationshipMemoryStore._ensure_table() is safe to call multiple times."""
    RelationshipMemoryStore(conn=pg_conn)
    store2 = RelationshipMemoryStore(conn=pg_conn)
    mem = store2.get("integ-idempotent-001")
    assert mem.total_calls == 0
