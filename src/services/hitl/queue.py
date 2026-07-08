"""HITLQueue — durable queue for REQUIRE_HUMAN decisions (V4 Ch15).

The durable, Postgres-backed successor to Sprint-020's in-memory
``HumanOversightRouter._queue`` — survives process restarts. Exposes a
``route()`` method with the exact signature of
:class:`src.libs.ai_safety.human_oversight.HumanOversightRouter` so it can
be wired into ``GovernanceLayer``/``AIGovernanceService`` wherever a
``HumanOversightRouter`` is accepted today (both satisfy the same
structural port — see ``ai_governance.governance_layer.HumanOversightRouterPort``).

Architecture: V4 Ch15 (Human Oversight).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from src.libs.contracts.models.hitl import HITLItem, HITLPriority
from src.libs.contracts.primitives import TenantId
from src.libs.event_bus.publisher import Publisher

from .ports import HITLQueueRepositoryPort

DEFAULT_SLA_MINUTES: dict[HITLPriority, int] = {
    HITLPriority.CRITICAL: 5,
    HITLPriority.HIGH: 30,
    HITLPriority.MEDIUM: 240,
}
"""Sprint-023.md's own SLA tiers — not sourced from Volume 4 (see pre-execution review)."""

_PRIORITY_ORDER: dict[HITLPriority, int] = {
    HITLPriority.CRITICAL: 0,
    HITLPriority.HIGH: 1,
    HITLPriority.MEDIUM: 2,
}


class HITLQueue:
    """Durable REQUIRE_HUMAN work queue, backed by the ``hitl_queue`` table."""

    def __init__(
        self,
        repository: HITLQueueRepositoryPort,
        publisher: Publisher | None = None,
        sla_minutes_by_priority: dict[HITLPriority, int] | None = None,
    ) -> None:
        self._repo = repository
        self._publisher = publisher
        self._sla_minutes = sla_minutes_by_priority or DEFAULT_SLA_MINUTES

    def enqueue(
        self,
        tenant_id: TenantId,
        call_id: str,
        reason: str,
        *,
        priority: HITLPriority = HITLPriority.MEDIUM,
        context: dict[str, object] | None = None,
    ) -> HITLItem:
        """Add a REQUIRE_HUMAN verdict (or supervisor escalation) to the durable queue."""
        now = datetime.now(UTC)
        item = HITLItem(
            hitl_item_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            call_id=call_id,
            reason=reason,
            priority=priority,
            enqueued_at=now,
            sla_deadline_at=now + timedelta(minutes=self._sla_minutes[priority]),
            context=context or {},
        )
        self._repo.create(item)
        if self._publisher is not None:
            self._publisher.publish(
                event_type="compliance.hitl.item_enqueued",
                tenant_id=tenant_id,
                payload={
                    "call_id": call_id,
                    "hitl_item_id": item.hitl_item_id,
                    "priority": priority.value,
                    "reason": reason,
                },
                correlation_id=call_id,
            )
        return item

    def route(self, call_id: str, tenant_id: str, reason: str) -> HITLItem:
        """``HumanOversightRouter``-compatible entry point (same signature, MEDIUM priority default)."""
        return self.enqueue(TenantId(tenant_id), call_id, reason)

    def dequeue(self, tenant_id: TenantId, supervisor_id: str) -> HITLItem | None:
        """Claim the next item for ``supervisor_id`` — highest priority, then oldest first."""
        pending = self._repo.find_pending(tenant_id)
        if not pending:
            return None
        item = min(pending, key=lambda i: (_PRIORITY_ORDER[i.priority], i.enqueued_at))
        self._repo.claim(tenant_id, item.hitl_item_id, supervisor_id, datetime.now(UTC))
        return self._repo.get(tenant_id, item.hitl_item_id)

    def list_pending(self, tenant_id: TenantId) -> tuple[HITLItem, ...]:
        """All items still awaiting pickup — what ``GET /hitl/queue`` returns."""
        return self._repo.find_pending(tenant_id)

    def get(self, tenant_id: TenantId, hitl_item_id: str) -> HITLItem | None:
        return self._repo.get(tenant_id, hitl_item_id)

    def resolve(self, tenant_id: TenantId, hitl_item_id: str) -> None:
        self._repo.resolve(tenant_id, hitl_item_id, datetime.now(UTC))
