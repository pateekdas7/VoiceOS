"""LiveTransferService — AI -> human transfer with context handoff (V5 Ch7.9-7.11).

Architecture: V5 Ch7 (Contact Center Platform — Live Transfer).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from src.libs.contracts.context import CustomerContext
from src.libs.contracts.decision import DecisionEnvelope
from src.libs.contracts.primitives import TenantId
from src.libs.event_bus.publisher import Publisher

from .agent_screen import AgentScreenContext, AgentScreenContextAssembler
from .router import AgentState, AgentStatus, SkillsBasedRouter


@runtime_checkable
class AudioBridgePort(Protocol):
    """Structural port for audio routing (real telephony is out of scope this sprint —
    ``FakeAudioBridge`` simulates it in tests, per Sprint-023.md's mock-backend table)."""

    def bridge(self, call_id: str, target_id: str) -> None: ...
    def mute_ai(self, call_id: str) -> None: ...
    def unmute_ai(self, call_id: str) -> None: ...
    def is_ai_muted(self, call_id: str) -> bool: ...


class TransferError(RuntimeError):
    """No available agent (or another transfer precondition failed)."""


@dataclass(frozen=True)
class TransferResult:
    """Outcome of a live transfer attempt (V5 Ch7.12)."""

    call_id: str
    routed_to_agent_id: str
    context: AgentScreenContext
    context_preserved: bool
    transferred_at: datetime


class LiveTransferService:
    """AI -> human transfer: assemble context, route, bridge audio, mute AI (V5 Ch7.9)."""

    def __init__(
        self,
        router: SkillsBasedRouter,
        audio_bridge: AudioBridgePort,
        publisher: Publisher | None = None,
    ) -> None:
        self._router = router
        self._audio_bridge = audio_bridge
        self._publisher = publisher

    def initiate_transfer(
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
        language: str | None = None,
    ) -> TransferResult:
        """Transfer ``call_id`` from AI to the best-matched available human agent.

        1. Assembles the :class:`AgentScreenContext`.
        2. Routes via :class:`SkillsBasedRouter`.
        3. Bridges audio to the agent's softphone; mutes the AI.
        4. Emits ``CallTransferred``.

        Raises :class:`TransferError` if no agent is available.
        """
        agent = self._router.find_agent(required_skills, language)
        if agent is None:
            raise TransferError(f"no available agent matching skills={required_skills!r} for call {call_id}")

        context = AgentScreenContextAssembler.assemble(
            call_id,
            tenant_id,
            customer_context,
            decision_envelope=decision_envelope,
            transcript=transcript,
            ai_summary=ai_summary,
            open_issues=open_issues,
        )

        self._audio_bridge.bridge(call_id, agent.agent_id)
        self._audio_bridge.mute_ai(call_id)
        self._router.update_state(agent.agent_id, status=AgentStatus.ON_CALL, current_call=call_id)

        result = TransferResult(
            call_id=call_id,
            routed_to_agent_id=agent.agent_id,
            context=context,
            context_preserved=True,
            transferred_at=datetime.now(UTC),
        )

        if self._publisher is not None:
            self._publisher.publish(
                event_type="saas.call.transferred",
                tenant_id=tenant_id,
                payload={
                    "call_id": call_id,
                    "reason": reason,
                    "routed_to_agent_id": agent.agent_id,
                    "context_preserved": True,
                },
                correlation_id=call_id,
            )

        return result

    def agent_state(self, agent_id: str) -> AgentState | None:
        return self._router.get(agent_id)
