"""HITLDashboard — escalation dashboard: queue depth, SLA status (V4 Ch15).

Architecture: V4 Ch15 (Human Oversight — Escalation Dashboard).
"""

from __future__ import annotations

from src.libs.contracts.primitives import TenantId

from .ports import HITLQueueRepositoryPort


class HITLDashboard:
    """Read-only queue-depth/SLA-status view for the supervisor escalation dashboard."""

    def __init__(self, repository: HITLQueueRepositoryPort) -> None:
        self._repo = repository

    def queue_depth(self, tenant_id: TenantId) -> int:
        """Number of items still PENDING for ``tenant_id`` (the ``hitl_queue_depth`` gauge source)."""
        return len(self._repo.find_pending(tenant_id))

    def sla_status(self, tenant_id: TenantId) -> dict[str, int]:
        """Counts of open items, split by whether their SLA has already been breached."""
        open_items = self._repo.find_open(tenant_id)
        breached = sum(1 for item in open_items if item.sla_breached)
        return {"open": len(open_items), "sla_breached": breached, "within_sla": len(open_items) - breached}
