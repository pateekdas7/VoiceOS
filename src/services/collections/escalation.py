"""EscalationWorkflow — auto-escalate calls on risk flags (V5 Ch4.6).

Escalation targets: ``HUMAN_AGENT`` | ``SUPERVISOR`` | ``LEGAL`` (V5 §5.13).
Full routing/paging pipeline is Sprint-027 scope — this workflow only records
that an escalation occurred and emits the domain event a future paging
integration would consume.

Architecture: V5 Ch4.6 (Escalation Workflow).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from src.libs.contracts.models.collections import EscalationRecord
from src.libs.contracts.primitives import CallId, CustomerId, TenantId
from src.libs.event_bus.publisher import Publisher
from src.libs.repositories.escalation import EscalationRepository

_ESCALATION_TRIGGERED_EVENT_TYPE = "saas.escalation.triggered"


class EscalationTarget(StrEnum):
    """Where an escalated call is routed (V5 §5.13)."""

    HUMAN_AGENT = "HUMAN_AGENT"
    SUPERVISOR = "SUPERVISOR"
    LEGAL = "LEGAL"


_CRITICAL_REASONS = frozenset({"ABUSE_DETECTED", "LEGAL_THREAT", "SELF_HARM_RISK"})


class EscalationWorkflow:
    """Auto-escalates a call on risk flags or explicit customer request (V5 Ch4.6)."""

    def __init__(self, repository: EscalationRepository, publisher: Publisher | None = None) -> None:
        self._repo = repository
        self._publisher = publisher

    def escalate(
        self,
        tenant_id: TenantId,
        call_id: CallId,
        customer_id: CustomerId,
        reason: str,
        escalated_to: EscalationTarget | None = None,
    ) -> EscalationRecord:
        """Record an escalation. ``escalated_to`` defaults to LEGAL for critical
        reasons (abuse/legal-threat/self-harm risk), else SUPERVISOR."""
        target = escalated_to or self._default_target(reason)
        escalation = EscalationRecord(
            escalation_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            call_id=call_id,
            customer_id=customer_id,
            reason=reason,
            escalated_to=target.value,
            escalated_at=datetime.now(UTC),
        )
        self._repo.create(escalation)
        if self._publisher is not None:
            self._publisher.publish(
                event_type=_ESCALATION_TRIGGERED_EVENT_TYPE,
                tenant_id=tenant_id,
                payload={
                    "call_id": str(call_id),
                    "customer_id": str(customer_id),
                    "escalation_id": escalation.escalation_id,
                    "reason": reason,
                    "escalated_to": target.value,
                },
                correlation_id=str(call_id),
            )
        return escalation

    def resolve(self, tenant_id: TenantId, escalation_id: str, resolution_notes: str) -> None:
        self._repo.resolve(tenant_id, escalation_id, datetime.now(UTC), resolution_notes)

    def find_by_call(self, tenant_id: TenantId, call_id: CallId) -> tuple[EscalationRecord, ...]:
        return self._repo.find_by_call(tenant_id, call_id)

    def list_for_tenant(self, tenant_id: TenantId) -> tuple[EscalationRecord, ...]:
        return self._repo.list_for_tenant(tenant_id)

    @staticmethod
    def _default_target(reason: str) -> EscalationTarget:
        if reason in _CRITICAL_REASONS:
            return EscalationTarget.LEGAL if reason == "LEGAL_THREAT" else EscalationTarget.SUPERVISOR
        return EscalationTarget.SUPERVISOR
