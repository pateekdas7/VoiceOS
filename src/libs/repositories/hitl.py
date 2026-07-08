"""HITLQueueRepository, HITLDecisionRepository — durable HITL queue storage (Sprint-023).

Backs the Postgres ``hitl_queue``/``hitl_decisions`` tables (migration 0020)
— the durable successor to Sprint-020's in-memory
``HumanOversightRouter._queue``. Survives process restarts.

Architecture: V4 Ch15 (Human Oversight).
"""

from __future__ import annotations

from typing import Any

from ..contracts.models.hitl import HITLDecision, HITLItem, HITLItemStatus, HITLPriority
from ..contracts.primitives import TenantId
from .base import BaseRepository

_QUEUE_TABLE = "hitl_queue"
_DECISIONS_TABLE = "hitl_decisions"

_QUEUE_COLUMNS = (
    "hitl_item_id",
    "tenant_id",
    "call_id",
    "reason",
    "priority",
    "status",
    "context",
    "enqueued_at",
    "claimed_by",
    "claimed_at",
    "resolved_at",
    "sla_deadline_at",
    "sla_breached",
)

_DECISION_COLUMNS = (
    "hitl_decision_id",
    "hitl_item_id",
    "tenant_id",
    "supervisor_id",
    "decision",
    "rationale",
    "decided_at",
)


class HITLQueueRepository(BaseRepository):
    """Tenant-scoped CRUD for the durable HITL queue."""

    def create(self, item: HITLItem) -> HITLItem:
        import json

        self._execute(
            f"""
            INSERT INTO {_QUEUE_TABLE} (
                hitl_item_id, tenant_id, call_id, reason, priority, status,
                context, enqueued_at, claimed_by, claimed_at, resolved_at,
                sla_deadline_at, sla_breached
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
            """,
            (
                item.hitl_item_id,
                item.tenant_id,
                item.call_id,
                item.reason,
                item.priority.value,
                item.status.value,
                json.dumps(item.context),
                item.enqueued_at,
                item.claimed_by,
                item.claimed_at,
                item.resolved_at,
                item.sla_deadline_at,
                item.sla_breached,
            ),
        )
        self._commit()
        return item

    def get(self, tenant_id: TenantId, hitl_item_id: str) -> HITLItem | None:
        row = self._tenant_select_one(
            _QUEUE_TABLE,
            _QUEUE_COLUMNS,
            tenant_id,
            extra_where="hitl_item_id = %s",
            extra_params=(hitl_item_id,),
        )
        return self._hydrate(row) if row is not None else None

    def find_pending(self, tenant_id: TenantId) -> tuple[HITLItem, ...]:
        """Items still awaiting supervisor pickup, oldest first (highest-priority queue view)."""
        rows = self._tenant_select(
            _QUEUE_TABLE,
            _QUEUE_COLUMNS,
            tenant_id,
            extra_where="status = %s",
            extra_params=(HITLItemStatus.PENDING.value,),
            order_by="enqueued_at ASC",
        )
        return tuple(self._hydrate(row) for row in rows)

    def find_open(self, tenant_id: TenantId | None = None) -> tuple[HITLItem, ...]:
        """PENDING or CLAIMED items — the SLAEnforcer's poll set.

        ``tenant_id`` is optional here (unlike every other lookup) because the
        SLA enforcer polls across all tenants; when omitted this bypasses the
        AR-8 mechanical scoping helpers and issues a direct SELECT.
        """
        cols = ", ".join(_QUEUE_COLUMNS)
        if tenant_id is not None:
            rows = self._tenant_select(
                _QUEUE_TABLE,
                _QUEUE_COLUMNS,
                tenant_id,
                extra_where="status IN (%s, %s)",
                extra_params=(HITLItemStatus.PENDING.value, HITLItemStatus.CLAIMED.value),
                order_by="enqueued_at ASC",
            )
            return tuple(self._hydrate(row) for row in rows)
        cur = self._execute(
            f"SELECT {cols} FROM {_QUEUE_TABLE} WHERE status IN (%s, %s) ORDER BY enqueued_at ASC",
            (HITLItemStatus.PENDING.value, HITLItemStatus.CLAIMED.value),
        )
        return tuple(self._hydrate(row) for row in cur.fetchall())

    def claim(self, tenant_id: TenantId, hitl_item_id: str, supervisor_id: str, claimed_at: Any) -> None:
        self._tenant_update(
            _QUEUE_TABLE,
            ("status", "claimed_by", "claimed_at"),
            (HITLItemStatus.CLAIMED.value, supervisor_id, claimed_at),
            tenant_id,
            extra_where="hitl_item_id = %s",
            extra_params=(hitl_item_id,),
        )

    def resolve(self, tenant_id: TenantId, hitl_item_id: str, resolved_at: Any) -> None:
        self._tenant_update(
            _QUEUE_TABLE,
            ("status", "resolved_at"),
            (HITLItemStatus.RESOLVED.value, resolved_at),
            tenant_id,
            extra_where="hitl_item_id = %s",
            extra_params=(hitl_item_id,),
        )

    def mark_sla_breached(self, tenant_id: TenantId, hitl_item_id: str) -> None:
        self._tenant_update(
            _QUEUE_TABLE,
            ("sla_breached",),
            (True,),
            tenant_id,
            extra_where="hitl_item_id = %s",
            extra_params=(hitl_item_id,),
        )

    def _hydrate(self, row: tuple[Any, ...]) -> HITLItem:
        import json

        (
            hitl_item_id,
            tenant_id,
            call_id,
            reason,
            priority,
            status,
            context_json,
            enqueued_at,
            claimed_by,
            claimed_at,
            resolved_at,
            sla_deadline_at,
            sla_breached,
        ) = row
        context = json.loads(context_json) if isinstance(context_json, str) else (context_json or {})
        return HITLItem(
            hitl_item_id=str(hitl_item_id),
            tenant_id=TenantId(str(tenant_id)),
            call_id=str(call_id),
            reason=reason,
            priority=HITLPriority(priority),
            status=HITLItemStatus(status),
            context=context,
            enqueued_at=enqueued_at,
            claimed_by=claimed_by,
            claimed_at=claimed_at,
            resolved_at=resolved_at,
            sla_deadline_at=sla_deadline_at,
            sla_breached=sla_breached,
        )


class HITLDecisionRepository(BaseRepository):
    """Append-only storage for supervisor decisions on HITL items."""

    def create(self, decision: HITLDecision) -> HITLDecision:
        self._execute(
            f"""
            INSERT INTO {_DECISIONS_TABLE} (
                hitl_decision_id, hitl_item_id, tenant_id, supervisor_id,
                decision, rationale, decided_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                decision.hitl_decision_id,
                decision.hitl_item_id,
                decision.tenant_id,
                decision.supervisor_id,
                decision.decision,
                decision.rationale,
                decision.decided_at,
            ),
        )
        self._commit()
        return decision

    def find_by_item(self, tenant_id: TenantId, hitl_item_id: str) -> tuple[HITLDecision, ...]:
        rows = self._tenant_select(
            _DECISIONS_TABLE,
            _DECISION_COLUMNS,
            tenant_id,
            extra_where="hitl_item_id = %s",
            extra_params=(hitl_item_id,),
            order_by="decided_at ASC",
        )
        return tuple(self._hydrate(row) for row in rows)

    def _hydrate(self, row: tuple[Any, ...]) -> HITLDecision:
        (
            hitl_decision_id,
            hitl_item_id,
            tenant_id,
            supervisor_id,
            decision,
            rationale,
            decided_at,
        ) = row
        return HITLDecision(
            hitl_decision_id=str(hitl_decision_id),
            hitl_item_id=str(hitl_item_id),
            tenant_id=TenantId(str(tenant_id)),
            supervisor_id=supervisor_id,
            decision=decision,
            rationale=rationale,
            decided_at=decided_at,
        )
