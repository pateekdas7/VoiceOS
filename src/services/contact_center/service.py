"""ContactCenterService — overall contact-center orchestration (V5 Ch7).

Architecture: V5 Ch7 (Contact Center Platform).
"""

from __future__ import annotations

from src.libs.contracts.context import CustomerContext
from src.libs.contracts.decision import DecisionEnvelope
from src.libs.contracts.primitives import TenantId

from . import metrics
from .agent_screen import AgentScreenContext
from .live_transfer import LiveTransferService, TransferResult
from .router import AgentState, SkillsBasedRouter
from .supervisor import MonitorSession, SupervisorService


class ContactCenterService:
    """Façade composing routing, live transfer, and supervisor operations (V5 Ch7)."""

    def __init__(
        self,
        router: SkillsBasedRouter,
        live_transfer: LiveTransferService,
        supervisor: SupervisorService,
    ) -> None:
        self._router = router
        self._live_transfer = live_transfer
        self._supervisor = supervisor

    def register_agent(self, agent: AgentState) -> None:
        self._router.register_agent(agent)

    def transfer_to_human(
        self,
        tenant_id: TenantId,
        call_id: str,
        reason: str,
        customer_context: CustomerContext,
        *,
        decision_envelope: DecisionEnvelope | None = None,
        transcript: tuple[str, ...] = (),
        ai_summary: str = "",
        open_issues: tuple[str, ...] = (),
        required_skills: tuple[str, ...] = (),
    ) -> TransferResult:
        """Escalate ``call_id`` from AI to a human agent (ESCALATE / REQUIRE_HUMAN / supervisor override)."""
        result = self._live_transfer.initiate_transfer(
            tenant_id,
            call_id,
            reason,
            customer_context,
            decision_envelope=decision_envelope,
            transcript=transcript,
            ai_summary=ai_summary,
            open_issues=open_issues,
            required_skills=required_skills,
        )
        metrics.record_transfer(reason)
        return result

    def monitor_call(self, tenant_id: TenantId, call_id: str, supervisor_id: str) -> MonitorSession:
        session = self._supervisor.monitor(tenant_id, call_id, supervisor_id)
        metrics.record_supervisor_intervention("monitor")
        return session

    def barge_in(self, tenant_id: TenantId, call_id: str, supervisor_id: str) -> None:
        self._supervisor.barge_in(tenant_id, call_id, supervisor_id)
        metrics.record_supervisor_intervention("barge_in")

    def override_call(self, tenant_id: TenantId, call_id: str, supervisor_id: str) -> None:
        self._supervisor.override(tenant_id, call_id, supervisor_id)
        metrics.record_supervisor_intervention("override")

    @property
    def agent_screen_context_type(self) -> type[AgentScreenContext]:
        """Exposed for callers that need the type without importing the submodule directly."""
        return AgentScreenContext
