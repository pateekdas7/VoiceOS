"""Persistent data models for the Human-In-The-Loop (HITL) platform.

The HITL queue is the durable, Postgres-backed successor to Sprint-020's
in-memory ``HumanOversightRouter._queue`` — REQUIRE_HUMAN verdicts (and
supervisor-escalation requests) survive a process restart and carry a
priority-derived SLA deadline.

Architecture: V4 Ch15 (Human Oversight), V5 Ch7 (Contact Center Platform).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..primitives import TenantId


class HITLPriority(StrEnum):
    """Priority tier for a HITL queue item — determines its SLA deadline."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"


class HITLItemStatus(StrEnum):
    """Lifecycle status of a HITL queue item."""

    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RESOLVED = "RESOLVED"


class HITLItem(BaseModel):
    """One REQUIRE_HUMAN verdict (or supervisor-escalation request) in the durable queue.

    ``sla_deadline_at`` is computed at enqueue time from ``priority`` (V4
    Ch15) — ``SLAEnforcer`` polls for items past this deadline still in
    ``PENDING``/``CLAIMED`` and emits ``HITLSLABreached``.
    """

    model_config = ConfigDict(frozen=True)

    hitl_item_id: str
    tenant_id: TenantId
    call_id: str
    reason: str
    priority: HITLPriority = HITLPriority.MEDIUM
    status: HITLItemStatus = HITLItemStatus.PENDING
    context: dict[str, object] = Field(default_factory=dict)
    """Supervisor-facing context snapshot (e.g. risk_score, violations, explanation)."""
    enqueued_at: datetime
    claimed_by: str | None = None
    claimed_at: datetime | None = None
    resolved_at: datetime | None = None
    sla_deadline_at: datetime
    sla_breached: bool = False


class HITLDecision(BaseModel):
    """An immutable record of a supervisor's decision on a HITL item (V4 Ch15 §15.12).

    Every decision requires a non-empty ``rationale`` — enforced by
    ``HumanReviewAPI``/``OverrideLogger``, not just at the model layer.
    """

    model_config = ConfigDict(frozen=True)

    hitl_decision_id: str
    hitl_item_id: str
    tenant_id: TenantId
    supervisor_id: str
    decision: str
    """Free-form decision label: 'APPROVE' | 'REJECT' | 'OVERRIDE' | 'ESCALATE_FURTHER'."""
    rationale: str = Field(min_length=1)
    decided_at: datetime


__all__ = [
    "HITLDecision",
    "HITLItem",
    "HITLItemStatus",
    "HITLPriority",
]
