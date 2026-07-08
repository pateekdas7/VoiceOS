"""RedisEventBusAdapter — wires ConversationEngine.EventBusPort to the real EventBus.

ConversationEngine (Sprint-012) was built against an ``EventBusPort``
Protocol and shipped with a ``_NoOpEventBus`` placeholder, explicitly
annotated "implemented by EventBus, Sprint-013". This adapter is that
implementation: it translates a sealed ``DecisionEnvelope`` into the
``decision.made`` domain event (V3 Ch3 §3.10 taxonomy) and durably appends
it to Redis Streams via the Sprint-013 ``Publisher``/``EventBus`` before
ConversationEngine proceeds to TTS synthesis (RI-4 commit-before-act).

Architecture: V3 Ch3 (Event Bus); V6 Ch6 EV-6 (DecisionEnvelope lineage).
"""

from __future__ import annotations

from src.libs.contracts.decision import DecisionEnvelope
from src.libs.event_bus.publisher import Publisher

DECISION_MADE_EVENT_TYPE = "decision.made"


class RedisEventBusAdapter:
    """Adapts the synchronous Sprint-013 ``Publisher`` to ConversationEngine's
    async ``EventBusPort`` (``publish(envelope: DecisionEnvelope) -> None``).

    The underlying Redis Streams append (XADD) is a sub-5ms operation
    (V3 Ch3 §3.14 target) and is not on the real-time audio path (RI-1
    guards the media thread, not per-turn orchestration), so it is called
    directly rather than offloaded to a thread executor.
    """

    def __init__(self, publisher: Publisher) -> None:
        self._publisher = publisher

    async def publish(self, envelope: DecisionEnvelope) -> str:
        """Durably persist ``envelope`` as a ``decision.made`` domain event.

        Returns:
            The real EventBus stream entry ID (Sprint-015 snapshot offset —
            see ``EventBusPort.publish()``), not the envelope's own UUID.
        """
        _event, entry_id = self._publisher.publish_with_entry_id(
            event_type=DECISION_MADE_EVENT_TYPE,
            tenant_id=envelope.tenant_id,
            payload={
                "envelope_id": envelope.envelope_id,
                "call_id": envelope.call_id,
                "response_plan_id": envelope.response_plan_id,
                "governance_status": envelope.governance_verdict.status.value,
                "decision_count": len(envelope.decisions),
            },
            correlation_id=envelope.call_id,
        )
        return entry_id
