"""EscalationRepository — call escalation tracking persistence (V5 Ch4.6).

Architecture: V5 Ch4.6 (Escalation Workflow).
"""

from __future__ import annotations

from typing import Any

from ..contracts.models.collections import EscalationRecord
from ..contracts.primitives import CallId, CustomerId, TenantId
from .base import BaseRepository

_TABLE = "escalation_records"

_ESCALATION_COLUMNS = (
    "escalation_id",
    "tenant_id",
    "call_id",
    "customer_id",
    "reason",
    "escalated_to",
    "escalated_at",
    "resolved_at",
    "resolution_notes",
)


class EscalationRepository(BaseRepository):
    """Tenant-scoped CRUD + lookup queries for the ``escalation_records`` domain."""

    def create(self, escalation: EscalationRecord) -> EscalationRecord:
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                escalation_id, tenant_id, call_id, customer_id, reason,
                escalated_to, escalated_at, resolved_at, resolution_notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                escalation.escalation_id,
                escalation.tenant_id,
                escalation.call_id,
                escalation.customer_id,
                escalation.reason,
                escalation.escalated_to,
                escalation.escalated_at,
                escalation.resolved_at,
                escalation.resolution_notes,
            ),
        )
        self._commit()
        return escalation

    def find_by_call(self, tenant_id: TenantId, call_id: CallId) -> tuple[EscalationRecord, ...]:
        rows = self._tenant_select(
            _TABLE,
            _ESCALATION_COLUMNS,
            tenant_id,
            extra_where="call_id = %s",
            extra_params=(call_id,),
            order_by="escalated_at DESC",
        )
        return tuple(self._hydrate(row) for row in rows)

    def resolve(self, tenant_id: TenantId, escalation_id: str, resolved_at: Any, resolution_notes: str) -> None:
        self._tenant_update(
            _TABLE,
            ("resolved_at", "resolution_notes"),
            (resolved_at, resolution_notes),
            tenant_id,
            extra_where="escalation_id = %s",
            extra_params=(escalation_id,),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _hydrate(self, row: tuple[Any, ...]) -> EscalationRecord:
        (
            escalation_id,
            tenant_id,
            call_id,
            customer_id,
            reason,
            escalated_to,
            escalated_at,
            resolved_at,
            resolution_notes,
        ) = row
        return EscalationRecord(
            escalation_id=str(escalation_id),
            tenant_id=TenantId(tenant_id),
            call_id=CallId(call_id),
            customer_id=CustomerId(customer_id),
            reason=reason,
            escalated_to=escalated_to,
            escalated_at=escalated_at,
            resolved_at=resolved_at,
            resolution_notes=resolution_notes,
        )
