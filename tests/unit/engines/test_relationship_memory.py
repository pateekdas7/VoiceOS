"""Unit tests for RelationshipMemoryStore using in-memory Postgres stub.

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

# ---------------------------------------------------------------------------
# Minimal in-memory Postgres stub for unit tests (no real DB connection)
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Minimal cursor stub that simulates an in-memory table."""

    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store
        self._last_result: Any = None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        sql_upper = sql.strip().upper()

        if "CREATE TABLE IF NOT EXISTS" in sql_upper:
            return  # DDL — no-op

        if "SELECT" in sql_upper and params:
            customer_id = params[0]
            self._last_result = self._store.get(customer_id)
            return

        if "INSERT INTO" in sql_upper and params:
            # Upsert
            (
                customer_id,
                total_calls,
                ptp_json,
                sent_json,
                best_contact_time,
                preferred_language,
                escalation_count,
                last_call_outcome,
            ) = params
            self._store[customer_id] = (
                total_calls,
                ptp_json,
                sent_json,
                best_contact_time,
                preferred_language,
                escalation_count,
                last_call_outcome,
            )

    def fetchone(self) -> Any:
        return self._last_result


class _FakeConn:
    """Minimal connection stub backed by an in-memory dict."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._cursor = _FakeCursor(self._store)

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn() -> _FakeConn:
    return _FakeConn()


@pytest.fixture
def store(conn: _FakeConn) -> RelationshipMemoryStore:
    return RelationshipMemoryStore(conn=conn)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_returns_empty_for_unknown_customer(store: RelationshipMemoryStore) -> None:
    """get() on a new customer_id returns empty RelationshipMemory."""
    mem = store.get("cust-new-001")
    assert isinstance(mem, RelationshipMemory)
    assert mem.customer_id == "cust-new-001"
    assert mem.total_calls == 0
    assert mem.ptp_history == ()
    assert mem.sentiment_history == ()
    assert mem.escalation_count == 0
    assert mem.last_call_outcome == ""


def test_update_increments_total_calls(store: RelationshipMemoryStore) -> None:
    """update() with a CallSummary increments total_calls by 1."""
    summary = CallSummary(
        call_id="call-001",
        sentiment="neutral",
        outcome="callback_scheduled",
    )
    store.update("cust-001", summary)
    mem = store.get("cust-001")
    assert mem.total_calls == 1


def test_update_twice_total_calls_is_two(store: RelationshipMemoryStore) -> None:
    """Two updates produce total_calls == 2."""
    summary = CallSummary(call_id="c", sentiment="neutral", outcome="ok")
    store.update("cust-002", summary)
    store.update("cust-002", summary)
    mem = store.get("cust-002")
    assert mem.total_calls == 2


def test_update_appends_sentiment_history(store: RelationshipMemoryStore) -> None:
    """update() appends sentiment to history; order preserved."""
    store.update("cust-003", CallSummary(call_id="c1", sentiment="positive", outcome="ptp_made"))
    store.update("cust-003", CallSummary(call_id="c2", sentiment="negative", outcome="dispute_raised"))
    mem = store.get("cust-003")
    assert "positive" in mem.sentiment_history
    assert "negative" in mem.sentiment_history
    assert mem.sentiment_history[0] == "positive"
    assert mem.sentiment_history[1] == "negative"


def test_update_appends_ptp_history(store: RelationshipMemoryStore) -> None:
    """update() with a ptp record appends it to ptp_history."""
    ptp = PromiseRecord(
        call_id="call-ptp",
        promised_date="2026-07-10",
        amount_minor=500000,
        fulfilled=False,
    )
    summary = CallSummary(
        call_id="call-ptp",
        sentiment="positive",
        outcome="ptp_made",
        ptp=ptp,
    )
    store.update("cust-004", summary)
    mem = store.get("cust-004")
    assert len(mem.ptp_history) == 1
    assert mem.ptp_history[0].promised_date == "2026-07-10"
    assert mem.ptp_history[0].amount_minor == 500000


def test_update_escalation_count(store: RelationshipMemoryStore) -> None:
    """Escalated call increments escalation_count."""
    store.update("cust-005", CallSummary(call_id="c1", sentiment="hostile", outcome="escalated", escalated=True))
    mem = store.get("cust-005")
    assert mem.escalation_count == 1


def test_update_non_escalated_no_increment(store: RelationshipMemoryStore) -> None:
    """Non-escalated call does not increment escalation_count."""
    store.update(
        "cust-006", CallSummary(call_id="c1", sentiment="neutral", outcome="callback_scheduled", escalated=False)
    )
    mem = store.get("cust-006")
    assert mem.escalation_count == 0


def test_update_last_call_outcome(store: RelationshipMemoryStore) -> None:
    """update() persists last_call_outcome."""
    store.update("cust-007", CallSummary(call_id="c1", sentiment="positive", outcome="ptp_made"))
    mem = store.get("cust-007")
    assert mem.last_call_outcome == "ptp_made"


def test_update_preferred_language(store: RelationshipMemoryStore) -> None:
    """update() persists preferred_language from CallSummary."""
    store.update("cust-008", CallSummary(call_id="c1", sentiment="neutral", outcome="ok", language="en-IN"))
    mem = store.get("cust-008")
    assert mem.preferred_language == "en-IN"


def test_update_best_contact_time(store: RelationshipMemoryStore) -> None:
    """update() persists best_contact_time from CallSummary."""
    store.update("cust-009", CallSummary(call_id="c1", sentiment="neutral", outcome="ok", contact_time_label="morning"))
    mem = store.get("cust-009")
    assert mem.best_contact_time == "morning"


def test_multiple_customers_isolated(store: RelationshipMemoryStore) -> None:
    """Separate customer_ids maintain isolated relationship memory."""
    store.update("cust-A", CallSummary(call_id="c1", sentiment="positive", outcome="ptp"))
    store.update("cust-B", CallSummary(call_id="c2", sentiment="negative", outcome="dispute"))
    mem_a = store.get("cust-A")
    mem_b = store.get("cust-B")
    assert "positive" in mem_a.sentiment_history
    assert "negative" in mem_b.sentiment_history


def test_relationship_memory_is_frozen() -> None:
    """RelationshipMemory is a frozen Pydantic model."""
    from pydantic import ValidationError

    mem = RelationshipMemory(customer_id="cust-frozen")
    with pytest.raises(ValidationError):
        mem.total_calls = 99  # type: ignore[misc]


def test_promise_record_fields() -> None:
    """PromiseRecord carries required fields."""
    ptp = PromiseRecord(
        call_id="c1",
        promised_date="2026-07-10",
        amount_minor=100000,
    )
    assert ptp.call_id == "c1"
    assert ptp.promised_date == "2026-07-10"
    assert ptp.amount_minor == 100000
    assert ptp.fulfilled is False


def test_call_summary_defaults() -> None:
    """CallSummary has sensible defaults."""
    s = CallSummary(call_id="c1", sentiment="neutral", outcome="ok")
    assert s.escalated is False
    assert s.ptp is None
    assert s.contact_time_label == ""
    assert s.language == "hi-IN"
