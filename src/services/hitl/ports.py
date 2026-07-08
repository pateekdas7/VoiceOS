"""Structural ports for the HITL platform (Sprint-023).

Repository parameters are typed against these Protocols (not the concrete
``HITLQueueRepository``/``HITLDecisionRepository`` classes) so unit tests can
inject in-memory fakes without needing real Postgres — same boundary-safe
"Protocol-injection" convention as ``CILPort``/``EventBusPort``
(``conversation_engine.engine``) and ``DNDStatusPort`` (``campaign_management
.scheduler``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from src.libs.contracts.models.hitl import HITLDecision, HITLItem
from src.libs.contracts.primitives import TenantId


@runtime_checkable
class HITLQueueRepositoryPort(Protocol):
    """Structural port matching ``HITLQueueRepository``'s public surface."""

    def create(self, item: HITLItem) -> HITLItem: ...
    def get(self, tenant_id: TenantId, hitl_item_id: str) -> HITLItem | None: ...
    def find_pending(self, tenant_id: TenantId) -> tuple[HITLItem, ...]: ...
    def find_open(self, tenant_id: TenantId | None = None) -> tuple[HITLItem, ...]: ...
    def claim(self, tenant_id: TenantId, hitl_item_id: str, supervisor_id: str, claimed_at: datetime) -> None: ...
    def resolve(self, tenant_id: TenantId, hitl_item_id: str, resolved_at: datetime) -> None: ...
    def mark_sla_breached(self, tenant_id: TenantId, hitl_item_id: str) -> None: ...


@runtime_checkable
class HITLDecisionRepositoryPort(Protocol):
    """Structural port matching ``HITLDecisionRepository``'s public surface."""

    def create(self, decision: HITLDecision) -> HITLDecision: ...
    def find_by_item(self, tenant_id: TenantId, hitl_item_id: str) -> tuple[HITLDecision, ...]: ...
