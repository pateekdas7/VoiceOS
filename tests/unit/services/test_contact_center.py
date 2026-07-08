"""Unit tests for the Contact Center Platform (Sprint-023): skills-based routing,
live transfer, supervisor monitor/barge-in/override.

Architecture: V5 Ch7 (Contact Center Platform); V4 Ch15 (Human Oversight).
"""

from __future__ import annotations

import pytest

from src.libs.contracts.context import ConsentStatus, ContactInfo, CustomerContext, PartyInfo
from src.libs.contracts.primitives import CustomerId, PhoneNumber, TenantId
from src.services.contact_center.live_transfer import LiveTransferService, TransferError
from src.services.contact_center.router import AgentState, AgentStatus, SkillsBasedRouter
from src.services.contact_center.supervisor import SupervisorService
from tests.fixtures.audio_bridge import FakeAudioBridge

TENANT = TenantId("tenant-a")


def _make_customer_context() -> CustomerContext:
    return CustomerContext(
        customer_id=CustomerId("cust-1"),
        tenant_id=TENANT,
        primary_party=PartyInfo(
            party_id=CustomerId("cust-1"),
            role="BORROWER",
            name="Asha Rao",
            contact=ContactInfo(phone_number=PhoneNumber("+919876543210")),
        ),
        consent_status=ConsentStatus.GRANTED,
        call_id="call-1",
    )


# ---------------------------------------------------------------------------
# SkillsBasedRouter
# ---------------------------------------------------------------------------


class TestSkillsBasedRouter:
    def test_find_agent_matches_required_skills(self) -> None:
        router = SkillsBasedRouter()
        router.register_agent(AgentState(agent_id="a1", skills=("hindi", "collections")))
        found = router.find_agent(required_skills=("hindi",))
        assert found is not None
        assert found.agent_id == "a1"

    def test_find_agent_returns_none_when_no_match(self) -> None:
        router = SkillsBasedRouter()
        router.register_agent(AgentState(agent_id="a1", skills=("hindi",)))
        assert router.find_agent(required_skills=("tamil",)) is None

    def test_find_agent_skips_unavailable_agents(self) -> None:
        router = SkillsBasedRouter()
        router.register_agent(AgentState(agent_id="a1", status=AgentStatus.ON_CALL, skills=("hindi",)))
        assert router.find_agent(required_skills=("hindi",)) is None

    def test_update_state_changes_status(self) -> None:
        router = SkillsBasedRouter()
        router.register_agent(AgentState(agent_id="a1"))
        updated = router.update_state("a1", status=AgentStatus.WRAP_UP, current_call="call-9")
        assert updated.status == AgentStatus.WRAP_UP
        assert updated.current_call == "call-9"


# ---------------------------------------------------------------------------
# LiveTransferService
# ---------------------------------------------------------------------------


class TestLiveTransferService:
    def test_live_transfer_context_includes_transcript(self) -> None:
        """AC: LiveTransferService assembles AgentScreenContext including
        CustomerContext, transcript, AI summary."""
        router = SkillsBasedRouter()
        router.register_agent(AgentState(agent_id="agent-1", skills=("collections",)))
        service = LiveTransferService(router, FakeAudioBridge())

        result = service.initiate_transfer(
            TENANT,
            "call-1",
            "REQUIRE_HUMAN",
            _make_customer_context(),
            transcript=("Agent: Hello", "Customer: I need help"),
            ai_summary="Customer disputes the outstanding amount.",
            open_issues=("dispute_amount",),
            required_skills=("collections",),
        )

        assert result.context.transcript == ("Agent: Hello", "Customer: I need help")
        assert result.context.ai_summary == "Customer disputes the outstanding amount."
        assert result.context.customer_context.customer_id == CustomerId("cust-1")
        assert result.context.open_issues == ("dispute_amount",)

    def test_transfer_mutes_ai_and_bridges_audio(self) -> None:
        router = SkillsBasedRouter()
        router.register_agent(AgentState(agent_id="agent-1"))
        bridge = FakeAudioBridge()
        service = LiveTransferService(router, bridge)

        service.initiate_transfer(TENANT, "call-1", "ESCALATE", _make_customer_context())

        assert bridge.is_ai_muted("call-1") is True
        assert bridge.bridged["call-1"] == "agent-1"

    def test_transfer_raises_when_no_agent_available(self) -> None:
        router = SkillsBasedRouter()
        service = LiveTransferService(router, FakeAudioBridge())
        with pytest.raises(TransferError):
            service.initiate_transfer(TENANT, "call-1", "ESCALATE", _make_customer_context())

    def test_transfer_marks_agent_on_call(self) -> None:
        router = SkillsBasedRouter()
        router.register_agent(AgentState(agent_id="agent-1"))
        service = LiveTransferService(router, FakeAudioBridge())

        service.initiate_transfer(TENANT, "call-1", "ESCALATE", _make_customer_context())

        agent = router.get("agent-1")
        assert agent is not None
        assert agent.status == AgentStatus.ON_CALL
        assert agent.current_call == "call-1"


# ---------------------------------------------------------------------------
# SupervisorService
# ---------------------------------------------------------------------------


class TestSupervisorService:
    def test_supervisor_monitor_does_not_interrupt_call(self) -> None:
        """AC: Supervisor monitor does not interrupt call (read-only)."""
        bridge = FakeAudioBridge()
        service = SupervisorService(bridge)

        session = service.monitor(TENANT, "call-1", "sup-1")

        assert session.call_id == "call-1"
        assert bridge.is_ai_muted("call-1") is False

    def test_supervisor_barge_in_mutes_ai(self) -> None:
        """AC: Supervisor barge-in mutes AI."""
        bridge = FakeAudioBridge()
        service = SupervisorService(bridge)

        service.barge_in(TENANT, "call-1", "sup-1")

        assert bridge.is_ai_muted("call-1") is True

    def test_supervisor_override_mutes_ai(self) -> None:
        bridge = FakeAudioBridge()
        service = SupervisorService(bridge)

        service.override(TENANT, "call-1", "sup-1")

        assert bridge.is_ai_muted("call-1") is True

    def test_supervisor_release_unmutes_ai(self) -> None:
        bridge = FakeAudioBridge()
        service = SupervisorService(bridge)
        service.barge_in(TENANT, "call-1", "sup-1")

        service.release(TENANT, "call-1", "sup-1")

        assert bridge.is_ai_muted("call-1") is False
