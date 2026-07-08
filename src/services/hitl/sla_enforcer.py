"""SLAEnforcer — monitors the HITL queue SLA; escalates on breach (V4 Ch15).

Sprint-023.md specifies a 60s poll loop; ``check_and_escalate`` is the
single poll tick (the caller — a scheduler/cron in Phase 2 deployment —
supplies the loop, matching every other "library, not a bound process"
service in this codebase, see CPU_NODE_STATE.md §8.1/TT-006).

Architecture: V4 Ch15 (Human Oversight — SLA enforcement/escalation).
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.libs.contracts.models.hitl import HITLItem
from src.libs.contracts.primitives import TenantId
from src.libs.event_bus.publisher import Publisher

from .ports import HITLQueueRepositoryPort

POLL_INTERVAL_SECONDS = 60


class SLAEnforcer:
    """Polls the HITL queue for items past their priority's SLA deadline."""

    def __init__(self, repository: HITLQueueRepositoryPort, publisher: Publisher | None = None) -> None:
        self._repo = repository
        self._publisher = publisher

    def check_and_escalate(
        self, tenant_id: TenantId | None = None, *, now: datetime | None = None
    ) -> tuple[HITLItem, ...]:
        """Check open (PENDING/CLAIMED) items against their SLA deadline; emit ``HITLSLABreached`` for each new breach.

        ``tenant_id=None`` polls across all tenants (the SLAEnforcer runs as
        a single cross-tenant background process, unlike per-request PEPs).
        Already-breached items are not re-emitted (idempotent per tick).
        """
        current_time = now or datetime.now(UTC)
        breached: list[HITLItem] = []
        for item in self._repo.find_open(tenant_id):
            if item.sla_breached or current_time < item.sla_deadline_at:
                continue
            self._repo.mark_sla_breached(item.tenant_id, item.hitl_item_id)
            age_seconds = int((current_time - item.enqueued_at).total_seconds())
            if self._publisher is not None:
                self._publisher.publish(
                    event_type="compliance.hitl.sla_breached",
                    tenant_id=item.tenant_id,
                    payload={
                        "call_id": item.call_id,
                        "hitl_item_id": item.hitl_item_id,
                        "priority": item.priority.value,
                        "age_seconds": age_seconds,
                    },
                    correlation_id=item.call_id,
                )
            breached.append(item)
        return tuple(breached)
