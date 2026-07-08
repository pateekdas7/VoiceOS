"""Unit tests for src/libs/contracts/events/envelope.py.

Tests: EventEnvelope creation, event_id uniqueness (each instance gets a
       different UUID), serialisation round-trip, DomainEvent base class,
       CorrelationId/CausationId typing.

AC-1: events module types pass mypy --strict.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from pydantic import ValidationError

from src.libs.contracts.events.envelope import (
    CausationId,
    CorrelationId,
    DomainEvent,
    EventEnvelope,
    EventMetadata,
)
from src.libs.contracts.primitives import TenantId

# ---------------------------------------------------------------------------
# EventEnvelope creation
# ---------------------------------------------------------------------------


class TestEventEnvelopeCreation:
    def test_minimal_envelope(self) -> None:
        env = EventEnvelope(
            event_type="call.started",
            tenant_id=TenantId("tenant-xyz"),
            correlation_id="corr-001",
            trace_id="trace-001",
        )
        assert env.event_type == "call.started"
        assert env.tenant_id == "tenant-xyz"
        assert env.version == 1

    def test_event_id_auto_generated(self) -> None:
        env = EventEnvelope(
            event_type="ptp.captured",
            tenant_id=TenantId("tenant-xyz"),
            correlation_id="corr-001",
            trace_id="trace-001",
        )
        assert env.event_id is not None
        assert len(env.event_id) > 0

    def test_event_id_is_unique_per_instance(self) -> None:
        """Each EventEnvelope must receive a distinct UUID (AC-6 / EV-7)."""
        envs = [
            EventEnvelope(
                event_type="call.ended",
                tenant_id=TenantId("tenant-xyz"),
                correlation_id="corr-001",
                trace_id="trace-001",
            )
            for _ in range(10)
        ]
        ids = {e.event_id for e in envs}
        assert len(ids) == 10, "Each EventEnvelope must have a unique event_id"

    def test_with_payload(self) -> None:
        env = EventEnvelope(
            event_type="ptp.captured",
            tenant_id=TenantId("tenant-xyz"),
            correlation_id="corr-001",
            trace_id="trace-001",
            payload={"ptp_id": "ptp-001", "amount_minor": 50000},
        )
        assert env.payload["ptp_id"] == "ptp-001"

    def test_causation_id_optional(self) -> None:
        env = EventEnvelope(
            event_type="consent.revoked",
            tenant_id=TenantId("tenant-xyz"),
            correlation_id="corr-001",
            trace_id="trace-001",
            causation_id=None,
        )
        assert env.causation_id is None

    def test_causation_id_present(self) -> None:
        env = EventEnvelope(
            event_type="consent.revoked",
            tenant_id=TenantId("tenant-xyz"),
            correlation_id="corr-001",
            trace_id="trace-001",
            causation_id="prior-event-id",
        )
        assert env.causation_id == "prior-event-id"

    def test_occurred_at_auto_generated(self) -> None:
        before = datetime.utcnow()
        env = EventEnvelope(
            event_type="call.started",
            tenant_id=TenantId("tenant-xyz"),
            correlation_id="corr-001",
            trace_id="trace-001",
        )
        after = datetime.utcnow()
        assert before <= env.occurred_at <= after


# ---------------------------------------------------------------------------
# EventEnvelope immutability
# ---------------------------------------------------------------------------


class TestEventEnvelopeImmutability:
    def test_event_type_mutation_raises(self) -> None:
        env = EventEnvelope(
            event_type="call.started",
            tenant_id=TenantId("tenant-xyz"),
            correlation_id="corr-001",
            trace_id="trace-001",
        )
        with pytest.raises(ValidationError):
            env.event_type = "tampered"  # type: ignore[misc]

    def test_tenant_id_mutation_raises(self) -> None:
        env = EventEnvelope(
            event_type="call.started",
            tenant_id=TenantId("tenant-xyz"),
            correlation_id="corr-001",
            trace_id="trace-001",
        )
        with pytest.raises(ValidationError):
            env.tenant_id = TenantId("other-tenant")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# JSON serialisation round-trip
# ---------------------------------------------------------------------------


class TestEventEnvelopeSerialization:
    def test_json_round_trip(self) -> None:
        env = EventEnvelope(
            event_type="ptp.captured",
            tenant_id=TenantId("tenant-xyz"),
            correlation_id="corr-001",
            trace_id="trace-001",
            payload={"amount": 50000},
        )
        j = env.model_dump_json()
        env2 = EventEnvelope.model_validate_json(j)
        assert env2.event_id == env.event_id
        assert env2.event_type == env.event_type
        assert env2.tenant_id == env.tenant_id

    def test_json_contains_all_required_keys(self) -> None:
        env = EventEnvelope(
            event_type="call.ended",
            tenant_id=TenantId("tenant-xyz"),
            correlation_id="corr-001",
            trace_id="trace-001",
        )
        data = json.loads(env.model_dump_json())
        assert "event_id" in data
        assert "event_type" in data
        assert "version" in data
        assert "occurred_at" in data
        assert "tenant_id" in data
        assert "correlation_id" in data
        assert "trace_id" in data
        assert "payload" in data

    def test_payload_preserved_in_round_trip(self) -> None:
        env = EventEnvelope(
            event_type="consent.granted",
            tenant_id=TenantId("tenant-xyz"),
            correlation_id="corr-001",
            trace_id="trace-001",
            payload={"customer_id": "cust-001", "channel": "voice"},
        )
        env2 = EventEnvelope.model_validate_json(env.model_dump_json())
        assert env2.payload["customer_id"] == "cust-001"
        assert env2.payload["channel"] == "voice"


# ---------------------------------------------------------------------------
# DomainEvent base class
# ---------------------------------------------------------------------------


class TestDomainEvent:
    def test_domain_event_event_id_unique(self) -> None:
        events = [DomainEvent(tenant_id=TenantId("tenant-xyz")) for _ in range(5)]
        ids = {e.event_id for e in events}
        assert len(ids) == 5

    def test_domain_event_occurred_at_auto(self) -> None:
        e = DomainEvent(tenant_id=TenantId("tenant-xyz"))
        assert e.occurred_at is not None

    def test_domain_event_immutable(self) -> None:
        e = DomainEvent(tenant_id=TenantId("tenant-xyz"))
        with pytest.raises(ValidationError):
            e.tenant_id = TenantId("other")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EventMetadata
# ---------------------------------------------------------------------------


class TestEventMetadata:
    def test_metadata_creation(self) -> None:
        m = EventMetadata(
            event_type="ptp.captured",
            tenant_id=TenantId("tenant-xyz"),
        )
        assert m.event_type == "ptp.captured"
        assert m.schema_version == 1

    def test_metadata_immutable(self) -> None:
        m = EventMetadata(
            event_type="call.ended",
            tenant_id=TenantId("tenant-xyz"),
        )
        with pytest.raises(ValidationError):
            m.event_type = "tampered"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ID type aliases
# ---------------------------------------------------------------------------


class TestIdTypes:
    def test_correlation_id_is_str(self) -> None:
        cid: CorrelationId = "my-correlation-id"
        assert isinstance(cid, str)

    def test_causation_id_is_str(self) -> None:
        cid: CausationId = "prior-event-123"
        assert isinstance(cid, str)
