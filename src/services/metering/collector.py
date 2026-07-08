"""UsageCollector — event-driven usage capture (V5 Ch10, Sprint-024.md).

Subscribes to the domain events that carry billable facts and turns each one
into exactly one idempotent :class:`UsageEvent` row. Uses the *source*
event's ``event_id`` as the ``usage_event_id`` — combined with
``UsageRepository.record_usage()``'s ``ON CONFLICT ... DO NOTHING``, this is
what makes redelivery of the same domain event collapse to a single stored
usage row (Sprint-024 AC: "usage events are idempotent").

Call-minute usage is derived from the existing ``saas.call.dispositioned``
event (already carries ``duration_ms``) rather than a new ``CallCompleted``
event Sprint-024.md names but that exists nowhere in this codebase — see
CHANGELOG.md Sprint-024 deviations. STT/LLM/GPU usage are derived from the
net-new ``saas.stt.transcribed`` / ``saas.llm.generated`` / ``saas.gpu.allocated``
events (also added this sprint).

Each handler expects the event-bus payload to be the *full* serialized
domain event (``event.model_dump(mode="json")``) — Consumer only ever hands
handlers ``envelope.payload``, not the envelope itself, so ``event_id`` and
``tenant_id`` must travel inside the payload for a consumer to see them at
all (see ``src/libs/event_bus/router.py``'s ``EventHandler`` contract).

Architecture: V5 Ch10 (Usage Metering — UsageCollector).
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Protocol

from src.libs.contracts.models.billing import UsageEvent, UsageType
from src.libs.contracts.primitives import TenantId
from src.services.billing.rate_card import RateCard

from .aggregator import hour_bucket
from .metrics import record_usage_event

CALL_DISPOSITIONED_EVENT_TYPE = "saas.call.dispositioned"
STT_TRANSCRIBED_EVENT_TYPE = "saas.stt.transcribed"
LLM_GENERATED_EVENT_TYPE = "saas.llm.generated"
GPU_ALLOCATED_EVENT_TYPE = "saas.gpu.allocated"


class UsageRepositoryPort(Protocol):
    def record_usage(self, event: UsageEvent) -> UsageEvent: ...


class ConsumerPort(Protocol):
    def subscribe(self, event_type: str, handler: Any) -> None: ...


class UsageCollector:
    """Derives billable :class:`UsageEvent` rows from usage-producing domain events."""

    def __init__(self, usage_repository: UsageRepositoryPort, rate_card: RateCard) -> None:
        self._usage_repository = usage_repository
        self._rate_card = rate_card

    def register(self, consumer: ConsumerPort) -> None:
        """Subscribe every handler on ``consumer`` (a ``Consumer`` or test double)."""
        consumer.subscribe(CALL_DISPOSITIONED_EVENT_TYPE, self.handle_call_dispositioned)
        consumer.subscribe(STT_TRANSCRIBED_EVENT_TYPE, self.handle_stt_transcribed)
        consumer.subscribe(LLM_GENERATED_EVENT_TYPE, self.handle_llm_generated)
        consumer.subscribe(GPU_ALLOCATED_EVENT_TYPE, self.handle_gpu_allocated)

    def handle_call_dispositioned(self, payload: dict[str, Any]) -> None:
        duration_ms = int(payload["duration_ms"])
        minutes = math.ceil(duration_ms / 60_000)
        self._record(payload, UsageType.CALL_MINUTE, minutes)

    def handle_stt_transcribed(self, payload: dict[str, Any]) -> None:
        self._record(payload, UsageType.STT_TOKEN, int(payload["token_count"]))

    def handle_llm_generated(self, payload: dict[str, Any]) -> None:
        self._record(payload, UsageType.LLM_TOKEN, int(payload["token_count"]))

    def handle_gpu_allocated(self, payload: dict[str, Any]) -> None:
        seconds = math.ceil(float(payload["allocated_seconds"]))
        self._record(payload, UsageType.GPU_SECOND, seconds)

    def _record(self, payload: dict[str, Any], usage_type: UsageType, quantity: int) -> None:
        if quantity <= 0:
            return
        entry = self._rate_card.entries[usage_type]
        occurred_at_raw = payload.get("occurred_at")
        occurred_at = (
            datetime.fromisoformat(str(occurred_at_raw).replace("Z", "+00:00")) if occurred_at_raw else datetime.now()
        )
        event = UsageEvent(
            usage_event_id=str(payload["event_id"]),
            tenant_id=TenantId(str(payload["tenant_id"])),
            usage_type=usage_type,
            quantity=quantity,
            unit_cost_minor=entry.price_per_unit_minor // entry.unit_size,
            total_cost_minor=entry.total_cost_minor(quantity),
            currency=self._rate_card.currency,
            resource_id=str(payload.get("call_id", "")),
            occurred_at_bucket=hour_bucket(occurred_at),
        )
        self._usage_repository.record_usage(event)
        record_usage_event(usage_type.value)


__all__ = [
    "CALL_DISPOSITIONED_EVENT_TYPE",
    "GPU_ALLOCATED_EVENT_TYPE",
    "LLM_GENERATED_EVENT_TYPE",
    "STT_TRANSCRIBED_EVENT_TYPE",
    "ConsumerPort",
    "UsageCollector",
    "UsageRepositoryPort",
]
