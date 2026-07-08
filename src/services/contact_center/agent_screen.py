"""AgentScreenContext — the context packet assembled for a human agent (V5 Ch7.9).

V5 Ch7 specifies "transcript + decision lineage + customer/loan data" as the
pushed context; this concretizes that into a composable packet built from
the existing ``CustomerContext``/``DecisionEnvelope`` contracts plus an AI
summary and open-issues list (Sprint-023.md's own richer description —
neither literally named in the architecture chapter, see the pre-execution
review's noted deviation #6).

Architecture: V5 Ch7 (Contact Center Platform — Live Transfer).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.libs.contracts.context import CustomerContext
from src.libs.contracts.decision import DecisionEnvelope
from src.libs.contracts.primitives import TenantId


@dataclass(frozen=True)
class AgentScreenContext:
    """Everything a human agent needs on-screen the moment a call is transferred."""

    call_id: str
    tenant_id: TenantId
    customer_context: CustomerContext
    decision_envelope: DecisionEnvelope | None
    transcript: tuple[str, ...] = field(default=())
    ai_summary: str = ""
    open_issues: tuple[str, ...] = field(default=())
    assembled_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class AgentScreenContextAssembler:
    """Assembles an :class:`AgentScreenContext` at the moment of transfer/supervisor join."""

    @staticmethod
    def assemble(
        call_id: str,
        tenant_id: TenantId,
        customer_context: CustomerContext,
        *,
        decision_envelope: DecisionEnvelope | None = None,
        transcript: tuple[str, ...] = (),
        ai_summary: str = "",
        open_issues: tuple[str, ...] = (),
    ) -> AgentScreenContext:
        return AgentScreenContext(
            call_id=call_id,
            tenant_id=tenant_id,
            customer_context=customer_context,
            decision_envelope=decision_envelope,
            transcript=transcript,
            ai_summary=ai_summary,
            open_issues=open_issues,
        )
