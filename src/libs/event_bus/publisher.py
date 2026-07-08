"""Publisher — validated, enveloped event emission (V3 Ch3, V6 Ch6 EV-3).

Wraps ``EventBus.publish()`` so producers never construct a raw stream
write by hand: the EventEnvelope's required fields (event_type, version,
tenant_id, payload) are enforced by Pydantic validation on construction,
and ``event_id``/``occurred_at`` are always freshly generated (EV-7) while
``trace_id`` is filled in if the caller has none yet (EV-8).

Architecture: V3 Ch3 §3.7; V6 Ch6 §6.4 (EV-3), §6.9 (producing pattern).
"""

from __future__ import annotations

import uuid
from typing import Any

from src.libs.contracts.events.envelope import EventEnvelope

from .bus import EventBus


class Publisher:
    """Validates and publishes domain events through an EventBus.

    Usage:
        publisher = Publisher(event_bus)
        envelope = publisher.publish(
            event_type="call.started",
            tenant_id=tenant_id,
            payload={"call_id": call_id},
            correlation_id=turn_correlation_id,
        )
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def publish(
        self,
        event_type: str,
        tenant_id: Any,
        payload: dict[str, Any],
        correlation_id: str,
        causation_id: str | None = None,
        trace_id: str | None = None,
        version: int = 1,
    ) -> EventEnvelope:
        """Build, validate, and publish an EventEnvelope.

        Args:
            event_type: '<aggregate>.<event>' past-tense (EV-2).
            tenant_id: Tenant scope — required (AR-8); invalid/missing
                       values raise via EventEnvelope's Pydantic validation.
            payload: Domain-specific event data.
            correlation_id: Groups all events for one turn/request (EV-8).
            causation_id: The event_id of the direct cause, if any.
            trace_id: Distributed trace ID; auto-generated if not supplied.
            version: Schema version of the payload (EV-4), default 1.

        Returns:
            The published EventEnvelope (with server-assigned event_id).

        Raises:
            pydantic.ValidationError: If a required envelope field (e.g.
                tenant_id) is missing or invalid (EV-3 schema enforcement).
        """
        envelope, _entry_id = self._build_and_publish(
            event_type, tenant_id, payload, correlation_id, causation_id, trace_id, version
        )
        return envelope

    def publish_with_entry_id(
        self,
        event_type: str,
        tenant_id: Any,
        payload: dict[str, Any],
        correlation_id: str,
        causation_id: str | None = None,
        trace_id: str | None = None,
        version: int = 1,
    ) -> tuple[EventEnvelope, str]:
        """Like ``publish()``, but also returns the EventBus stream entry ID.

        Sprint-015: callers that need to resume replay from this exact point
        (``EventTailReplay``/``Snapshot.last_event_offset``) need the real
        Redis Streams entry ID (e.g. ``"1720051234567-0"``) — the
        EventEnvelope's own ``event_id`` is a UUID and is not a valid
        argument to ``XRANGE``. ``publish()`` is left unchanged (many
        existing callers only need the envelope) — this is an additive
        sibling method, not a breaking change to its return type.
        """
        return self._build_and_publish(event_type, tenant_id, payload, correlation_id, causation_id, trace_id, version)

    def _build_and_publish(
        self,
        event_type: str,
        tenant_id: Any,
        payload: dict[str, Any],
        correlation_id: str,
        causation_id: str | None,
        trace_id: str | None,
        version: int,
    ) -> tuple[EventEnvelope, str]:
        envelope = EventEnvelope(
            event_type=event_type,
            version=version,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            trace_id=trace_id or str(uuid.uuid4()),
            payload=payload,
        )
        entry_id = self._bus.publish(envelope)
        return envelope, entry_id
