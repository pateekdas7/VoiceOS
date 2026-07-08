"""OverrideLogger — logs human decisions with mandatory rationale (V4 Ch15 §15.12).

Every human override is audited: who reviewed, what the decision was, and
the rationale text. Feeds the AI-governance-board review process (periodic
review of override patterns).

Architecture: V4 Ch15 (Human Oversight — Override Audit).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from src.libs.audit.logger import AuditLogger
from src.libs.contracts.models.hitl import HITLDecision
from src.libs.contracts.primitives import TenantId
from src.libs.event_bus.publisher import Publisher

from .ports import HITLDecisionRepositoryPort, HITLQueueRepositoryPort


class OverrideLogger:
    """Records a supervisor's decision on a HITL item — rationale is mandatory."""

    def __init__(
        self,
        decision_repository: HITLDecisionRepositoryPort,
        queue_repository: HITLQueueRepositoryPort,
        audit_logger: AuditLogger | None = None,
        publisher: Publisher | None = None,
    ) -> None:
        self._decision_repo = decision_repository
        self._queue_repo = queue_repository
        self._audit_logger = audit_logger
        self._publisher = publisher

    def record_decision(
        self,
        tenant_id: TenantId,
        hitl_item_id: str,
        supervisor_id: str,
        decision: str,
        rationale: str,
    ) -> HITLDecision:
        """Persist the decision, resolve the queue item, and audit it.

        Raises ``ValueError`` if ``rationale`` is empty — every human
        override must be accompanied by a rationale (V4 Ch15 §15.12).
        """
        if not rationale.strip():
            raise ValueError("rationale is required for a HITL decision")

        now = datetime.now(UTC)
        record = HITLDecision(
            hitl_decision_id=str(uuid.uuid4()),
            hitl_item_id=hitl_item_id,
            tenant_id=tenant_id,
            supervisor_id=supervisor_id,
            decision=decision,
            rationale=rationale,
            decided_at=now,
        )
        self._decision_repo.create(record)
        self._queue_repo.resolve(tenant_id, hitl_item_id, now)

        if self._audit_logger is not None:
            self._audit_logger.record_hitl_decision(tenant_id, supervisor_id, hitl_item_id, decision, rationale)

        if self._publisher is not None:
            call_id = self._call_id_for(tenant_id, hitl_item_id)
            self._publisher.publish(
                event_type="compliance.hitl.decision_recorded",
                tenant_id=tenant_id,
                payload={
                    "call_id": call_id,
                    "hitl_item_id": hitl_item_id,
                    "supervisor_id": supervisor_id,
                    "decision": decision,
                    "rationale": rationale,
                },
                correlation_id=call_id or hitl_item_id,
            )

        return record

    def _call_id_for(self, tenant_id: TenantId, hitl_item_id: str) -> str:
        item = self._queue_repo.get(tenant_id, hitl_item_id)
        return item.call_id if item is not None else ""
