"""EventEnvelope and DomainEvent base types.

The EventEnvelope is the typed, versioned, tenant-scoped wrapper around every
domain event in VoiceOS. It is the cross-cutting substrate for:
  - Event log append-only storage (V3 Ch3)
  - Idempotent event dedup (V3 Ch8)
  - Deterministic replay (V3 Ch7)
  - Distributed tracing (V3 Ch17)
  - Audit trail (V4 Ch11)
  - Analytics (V5 Ch11)

Every domain event carries this envelope so consumers always have the context
they need without out-of-band lookups.

Architecture: V1 Appendix A; V3 Ch3; V6 Ch6 (EV-3); DocSuite-02 B.2;
              DocSuite-03.
Invariant: EV-8 (correlation/causation/trace IDs propagated on every hop).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..primitives import TenantId

# Typed string aliases for the three distributed-tracing IDs.
# These are NewType equivalents expressed as type aliases for readability.
CorrelationId = str
"""Groups all events and logs for one logical operation (a turn / request).
Set at ingress; propagated to every thread, async task, and service call (EV-8)."""

CausationId = str
"""The immediate prior event ID that caused this event (enables causal graph).
May be None when an event has no single prior cause."""


class EventMetadata(BaseModel):
    """Routing and classification metadata attached to every event.

    Separated from EventEnvelope to allow metadata-only reads by consumers
    that route events without deserializing the full payload.
    """

    model_config = ConfigDict(frozen=True)

    event_type: str
    """Domain event type in '<aggregate>.<event>' past-tense notation (EV-2).
    Examples: 'ptp.captured', 'call.ended', 'consent.revoked'."""

    schema_version: int = Field(default=1, ge=1)
    """Schema version. Bump only on breaking changes; add fields additively (EV-4/5)."""

    tenant_id: TenantId
    """Tenant scope (AR-8). Never optional — every event is tenant-scoped."""


class DomainEvent(BaseModel):
    """Abstract base for all VoiceOS domain events.

    Subclassed by each domain (call, loan, consent, campaign, billing…) in
    Sprint-002. Sprint-001 defines the base so EventEnvelope can reference it.

    Subclasses add typed ``payload`` fields. The EventEnvelope wraps the
    serialized payload for the event log; strongly-typed subclasses are used
    within services.

    Architecture: V3 Ch3; V6 Ch6 EV-1.
    """

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    """Globally unique event identifier (UUID).
    Also serves as the idempotency dedup key (V3 Ch8, EV-7)."""

    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    """UTC timestamp when this event occurred (not when it was persisted)."""

    tenant_id: TenantId
    """Tenant scope (AR-8)."""


class EventEnvelope(BaseModel):
    """The universal event wrapper for the VoiceOS event log.

    Every domain event is emitted wrapped in an EventEnvelope. Consumers
    read the envelope to route, dedup, and trace events before deserializing
    the payload.

    Immutability: frozen=True prevents post-construction mutation. The event
    log is append-only — envelopes are never modified after emission.

    NOTE on ``payload: dict[str, Any]``: this is an explicitly permitted use
    of Any (AC-6). The event log must accept any domain payload shape; each
    domain's consumer casts to its typed DomainEvent subclass. Serialisation
    round-trips preserve all fields.

    Architecture: V1 Appendix A; V3 Ch3; V6 Ch6 EV-3; DocSuite-02 B.2.
    Invariant: EV-7 (authoritative-effect events carry idempotency key).
               EV-8 (correlation_id, causation_id, trace_id always present).
    """

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    """Globally unique event identifier (UUID). Dedup key for idempotent consumers.
    Each EventEnvelope instance gets a fresh UUID (AC-6 / EV-7 compliance)."""

    event_type: str
    """Domain event type string (EV-2): '<aggregate>.<event>', past tense.
    Examples: 'call.started', 'ptp.captured', 'decision.made'."""

    version: int = Field(default=1, ge=1)
    """Schema version of the payload (EV-4). Bump on breaking payload changes."""

    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    """UTC wall-clock time when the event occurred (not persisted-at time)."""

    tenant_id: TenantId
    """Tenant scope (AR-8). Required on every event — no tenant-less events."""

    correlation_id: CorrelationId
    """Groups all events for one logical operation (turn/request). Set at ingress.
    Propagated across all thread/async/network/GPU hops (EV-8)."""

    causation_id: CausationId | None = None
    """The event_id of the direct cause, if any. Builds a causal graph (EV-8)."""

    trace_id: str
    """OpenTelemetry trace ID. Links to distributed trace spans (V3 Ch17, EV-8)."""

    payload: dict[str, Any] = Field(default_factory=dict)
    """The domain-specific event data.
    NOTE: dict[str, Any] is explicitly permitted here (AC-6) because the event
    log stores events from all domains and the payload schema varies per event_type.
    Typed consumers cast to their domain's DomainEvent subclass."""
