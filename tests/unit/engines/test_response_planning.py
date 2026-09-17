"""Unit tests for ResponsePlanningEngine — the CIL pipeline orchestrator.

Covers the Phase 1 wiring fixes (VoiceOS Path-A consolidation):
  - sentiment reaches RiskEngine.evaluate()
  - turn_index reaches DialoguePolicyEngine.evaluate()
  - real ConversationState (via ConversationStateIntelligence) reaches
    StrategyEngine.select() / GoalPlanner.plan(), replacing the old
    _infer_state() heuristic
  - state persists across turns when the caller passes the same tracker
  - NegotiationEngine.compute_move() receives a real customer offer amount
    (extracted from entity slots) and a real hardship signal, so ACCEPT/
    COUNTER/DECLINE/PROPOSE_PTP are all reachable, not just OFFER

Architecture: V2 Ch1, V2 Ch13 (Conversation State Intelligence).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from src.engines.adaptive_conversation.engine import AdaptiveConversationEngine
from src.engines.conversation_state.engine import ConversationStateIntelligence
from src.engines.conversation_state.schema import ConversationState
from src.engines.dialogue_policy.constraints import PolicyConstraintType
from src.engines.dialogue_policy.engine import DialoguePolicyEngine
from src.engines.emotion.engine import EmotionIntelligenceEngine
from src.engines.empathy.engine import EmpathyPlanner
from src.engines.entity_extraction.engine import EntityExtractor
from src.engines.goal_planner.engine import GoalPlanner
from src.engines.goal_planner.goals import Goal
from src.engines.intent.engine import IntentEngine
from src.engines.intent.model import IntentModel
from src.engines.intent.result import IntentResult
from src.engines.negotiation.engine import NegotiationEngine
from src.engines.negotiation.moves import NegotiationMove
from src.engines.response_planning.engine import ResponsePlanningEngine
from src.engines.risk.engine import RiskEngine
from src.engines.risk.flags import RiskFlag
from src.engines.risk.result import RiskAssessment
from src.engines.strategy.engine import StrategyEngine
from src.libs.contracts.context import (
    ConsentStatus,
    ContactInfo,
    CustomerContext,
    LoanSummary,
    OutstandingBalance,
    PartyInfo,
)
from src.libs.contracts.primitives import AccountId, Currency, CustomerId, Money, TenantId
from src.libs.contracts.response_plan import IntentLabel, StrategyLabel
from src.libs.contracts.turn import TurnInput, TurnRole

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(outstanding_minor: int = 500_000, dpd: int = 45) -> CustomerContext:
    loan = LoanSummary(
        account_id=AccountId("acc-001"),
        product_type="PERSONAL_LOAN",
        outstanding_balance=Money(amount_minor=outstanding_minor, currency=Currency.INR),
        dpd=dpd,
        total_overdue=Money(amount_minor=outstanding_minor, currency=Currency.INR),
    )
    return CustomerContext(
        customer_id=CustomerId("cust-001"),
        tenant_id=TenantId("tenant-001"),
        primary_party=PartyInfo(
            party_id=CustomerId("cust-001"),
            role="BORROWER",
            name="Test Customer",
            contact=ContactInfo(phone_number="+919876543210"),  # type: ignore[arg-type]
        ),
        loans=(loan,),
        outstanding=OutstandingBalance(
            total_outstanding=Money(amount_minor=outstanding_minor, currency=Currency.INR),
            total_overdue=Money(amount_minor=outstanding_minor, currency=Currency.INR),
            account_count=1,
        ),
        consent_status=ConsentStatus.GRANTED,
    )


def _make_turn(transcript: str, turn_index: int = 0, call_id: str = "call-001") -> TurnInput:
    return TurnInput(
        turn_id=str(uuid.uuid4()),
        call_id=call_id,
        tenant_id="tenant-001",
        role=TurnRole.CUSTOMER,
        transcript=transcript,
        segments=(),
        created_at=datetime.utcnow(),
        correlation_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        turn_index=turn_index,
    )


def _build_engine() -> ResponsePlanningEngine:
    return ResponsePlanningEngine(
        intent_engine=IntentEngine(IntentModel()),
        entity_extractor=EntityExtractor(),
        emotion_engine=EmotionIntelligenceEngine(),
        risk_engine=RiskEngine(),
        dialogue_policy_engine=DialoguePolicyEngine(),
        strategy_engine=StrategyEngine(),
        goal_planner=GoalPlanner(),
        negotiation_engine=NegotiationEngine(),
        empathy_planner=EmpathyPlanner(),
        adaptive_conv_engine=AdaptiveConversationEngine(),
    )


@pytest.fixture
def engine() -> ResponsePlanningEngine:
    return _build_engine()


# ---------------------------------------------------------------------------
# turn_index -> DialoguePolicyEngine
# ---------------------------------------------------------------------------


def test_turn_index_zero_requires_recording_disclosure(engine: ResponsePlanningEngine) -> None:
    plan, _ = engine.assemble(
        turn=_make_turn("main payment karna chahta hoon", turn_index=0),
        context=None,
        retrieval=[],
    )
    assert any(item.item_id == "RBI_RECORDING_DISCLOSURE" for item in plan.must_say)


def test_turn_index_nonzero_does_not_require_recording_disclosure(engine: ResponsePlanningEngine) -> None:
    plan, _ = engine.assemble(
        turn=_make_turn("main payment karna chahta hoon", turn_index=3),
        context=None,
        retrieval=[],
    )
    assert not any(item.item_id == "RBI_RECORDING_DISCLOSURE" for item in plan.must_say)


# ---------------------------------------------------------------------------
# sentiment -> RiskEngine (hostile-sentiment fallback, no direct abuse keyword)
# ---------------------------------------------------------------------------


def test_hostile_sentiment_alone_triggers_abuse_flag(engine: ResponsePlanningEngine) -> None:
    """'har roj ... bhai' matches EmotionIntelligenceEngine's HOSTILE pattern
    but no RiskEngine _ABUSE_PATTERNS keyword directly — only reachable via
    the sentiment=... fallback this Phase-1 fix wires through."""
    plan, envelope = engine.assemble(
        turn=_make_turn("har roj phone karte ho bhai"),
        context=None,
        retrieval=[],
    )
    risk_decision = next(d for d in envelope.decisions if d.reason.source_engine == "RiskEngine")
    assert "ABUSE_DETECTED" in risk_decision.reason.decision


# ---------------------------------------------------------------------------
# Real ConversationState tracking (replaces _infer_state heuristic)
# ---------------------------------------------------------------------------


def test_conversation_state_persists_across_turns_via_tracker(engine: ResponsePlanningEngine) -> None:
    tracker = ConversationStateIntelligence()
    assert tracker.current_state == ConversationState.GREETING

    engine.assemble(
        turn=_make_turn("hello", turn_index=0),
        context=None,
        retrieval=[],
        conversation_state_tracker=tracker,
    )
    assert tracker.current_state == ConversationState.IDENTITY_VERIFICATION

    engine.assemble(
        turn=_make_turn("mera naam Rohan hai", turn_index=1),
        context=None,
        retrieval=[],
        identity_verified=True,
        conversation_state_tracker=tracker,
    )
    assert tracker.current_state == ConversationState.DEBT_DISCUSSION


def test_no_tracker_supplied_defaults_to_fresh_greeting_state(engine: ResponsePlanningEngine) -> None:
    """Backward-compatible: callers that don't track state (e.g. simple
    single-turn tests) get an ephemeral GREETING-seeded tracker each call,
    never a stale/shared one."""
    plan1, _ = engine.assemble(turn=_make_turn("hello", turn_index=0), context=None, retrieval=[])
    plan2, _ = engine.assemble(turn=_make_turn("hello", turn_index=0), context=None, retrieval=[])
    assert plan1.plan_id != plan2.plan_id  # both ran independently, no shared mutable state


def test_abuse_forces_escalation_state(engine: ResponsePlanningEngine) -> None:
    tracker = ConversationStateIntelligence()
    engine.assemble(
        turn=_make_turn("gaali", turn_index=0),
        context=None,
        retrieval=[],
        conversation_state_tracker=tracker,
    )
    assert tracker.current_state == ConversationState.ESCALATION


# ---------------------------------------------------------------------------
# Real negotiation params (customer offer, hardship) reach compute_move()
# ---------------------------------------------------------------------------


def test_negotiation_receives_real_customer_offer_and_accepts(engine: ResponsePlanningEngine) -> None:
    """'teen hazaar ... abhi' extracts a PARTIAL_AMOUNT of Rs.3,000 (300,000
    minor units) - at/above the 50%-of-outstanding floor for a Rs.5,000
    outstanding balance, so NegotiationEngine should ACCEPT, not just emit
    the old always-OFFER-at-ceiling default."""
    tracker = ConversationStateIntelligence()
    tracker.transition(ConversationState.IDENTITY_VERIFICATION)
    tracker.transition(ConversationState.DEBT_DISCUSSION)
    tracker.transition(ConversationState.NEGOTIATION)

    context = _make_context(outstanding_minor=500_000)
    plan, envelope = engine.assemble(
        turn=_make_turn("main abhi teen hazaar de raha hoon", turn_index=2),
        context=context,
        retrieval=[],
        identity_verified=True,
        conversation_state_tracker=tracker,
    )

    assert plan.negotiation_envelope is not None
    assert plan.negotiation_envelope.proposed_amount_minor == 300_000
    assert plan.negotiation_envelope.proposed_date is not None
    neg_decision = next(d for d in envelope.decisions if d.reason.source_engine == "NegotiationEngine")
    assert "ACCEPT" in neg_decision.reason.decision
    assert tracker.current_state == ConversationState.COMMITMENT_CAPTURE


def test_negotiation_without_customer_offer_still_opens_at_ceiling(engine: ResponsePlanningEngine) -> None:
    """No customer offer extracted -> unchanged pre-fix behaviour (OFFER at
    ceiling) for a plain 'I want to negotiate' style turn."""
    tracker = ConversationStateIntelligence()
    tracker.transition(ConversationState.IDENTITY_VERIFICATION)
    tracker.transition(ConversationState.DEBT_DISCUSSION)
    tracker.transition(ConversationState.NEGOTIATION)

    context = _make_context(outstanding_minor=500_000)
    plan, _ = engine.assemble(
        turn=_make_turn("main payment de raha hoon", turn_index=2),
        context=context,
        retrieval=[],
        identity_verified=True,
        conversation_state_tracker=tracker,
    )
    assert plan.negotiation_envelope is not None
    assert plan.negotiation_envelope.proposed_amount_minor == 500_000  # ceiling


# ---------------------------------------------------------------------------
# _determine_next_state — direct unit coverage of the replacement heuristic
# ---------------------------------------------------------------------------


def _intent(label: IntentLabel) -> IntentResult:
    return IntentResult(label=label, confidence=0.9)


def _risk(flags: list[RiskFlag]) -> RiskAssessment:
    return RiskAssessment(flags=flags, escalation_required=False, human_handoff_required=False)


class TestDetermineNextState:
    def test_abuse_flag_forces_escalation(self) -> None:
        result = ResponsePlanningEngine._determine_next_state(
            current_state=ConversationState.DEBT_DISCUSSION,
            intent_result=_intent(IntentLabel.PAYMENT),
            risk_assessment=_risk([RiskFlag.ABUSE_DETECTED]),
            goal=Goal.COLLECT_FULL_PAYMENT,
            strategy_label=StrategyLabel.ASK,
            negotiation_move=None,
            identity_verified=True,
        )
        assert result == ConversationState.ESCALATION

    def test_disconnect_intent_forces_closing(self) -> None:
        result = ResponsePlanningEngine._determine_next_state(
            current_state=ConversationState.DEBT_DISCUSSION,
            intent_result=_intent(IntentLabel.DISCONNECT),
            risk_assessment=_risk([]),
            goal=Goal.END_CALL,
            strategy_label=StrategyLabel.CLOSE,
            negotiation_move=None,
            identity_verified=True,
        )
        assert result == ConversationState.CLOSING

    def test_greeting_always_advances_to_identity_verification(self) -> None:
        result = ResponsePlanningEngine._determine_next_state(
            current_state=ConversationState.GREETING,
            intent_result=_intent(IntentLabel.OTHER),
            risk_assessment=_risk([]),
            goal=Goal.VERIFY_IDENTITY,
            strategy_label=StrategyLabel.VERIFY,
            negotiation_move=None,
            identity_verified=False,
        )
        assert result == ConversationState.IDENTITY_VERIFICATION

    def test_identity_verification_holds_until_verified(self) -> None:
        result = ResponsePlanningEngine._determine_next_state(
            current_state=ConversationState.IDENTITY_VERIFICATION,
            intent_result=_intent(IntentLabel.OTHER),
            risk_assessment=_risk([]),
            goal=Goal.VERIFY_IDENTITY,
            strategy_label=StrategyLabel.VERIFY,
            negotiation_move=None,
            identity_verified=False,
        )
        assert result == ConversationState.IDENTITY_VERIFICATION

    def test_negotiation_accept_moves_to_commitment_capture(self) -> None:
        result = ResponsePlanningEngine._determine_next_state(
            current_state=ConversationState.NEGOTIATION,
            intent_result=_intent(IntentLabel.PAYMENT),
            risk_assessment=_risk([]),
            goal=Goal.COLLECT_PARTIAL_PAYMENT,
            strategy_label=StrategyLabel.NEGOTIATE,
            negotiation_move=NegotiationMove.ACCEPT,
            identity_verified=True,
        )
        assert result == ConversationState.COMMITMENT_CAPTURE

    def test_dispute_moves_to_objection_handling(self) -> None:
        result = ResponsePlanningEngine._determine_next_state(
            current_state=ConversationState.DEBT_DISCUSSION,
            intent_result=_intent(IntentLabel.DISPUTE),
            risk_assessment=_risk([RiskFlag.DISPUTE_CLAIM]),
            goal=Goal.HANDLE_DISPUTE,
            strategy_label=StrategyLabel.VERIFY,
            negotiation_move=None,
            identity_verified=True,
        )
        assert result == ConversationState.OBJECTION_HANDLING

    def test_no_change_returns_current_state(self) -> None:
        result = ResponsePlanningEngine._determine_next_state(
            current_state=ConversationState.DEBT_DISCUSSION,
            intent_result=_intent(IntentLabel.OTHER),
            risk_assessment=_risk([]),
            goal=Goal.COLLECT_FULL_PAYMENT,
            strategy_label=StrategyLabel.ASK,
            negotiation_move=None,
            identity_verified=True,
        )
        assert result == ConversationState.DEBT_DISCUSSION


# ---------------------------------------------------------------------------
# Illegal transition requests are rejected gracefully, never raised
# ---------------------------------------------------------------------------


def test_illegal_transition_request_is_swallowed_not_raised(engine: ResponsePlanningEngine) -> None:
    """POST_CALL is terminal (no outgoing transitions). A turn that would
    otherwise request a state change must not raise — the tracker just
    stays at POST_CALL."""
    tracker = ConversationStateIntelligence()
    tracker.transition(ConversationState.CLOSING)
    tracker.transition(ConversationState.POST_CALL)

    # Should not raise, even though the computed request (DEBT_DISCUSSION,
    # since current_state isn't GREETING/IDENTITY_VERIFICATION and nothing
    # else matches) is illegal from POST_CALL.
    engine.assemble(
        turn=_make_turn("hello again", turn_index=5),
        context=None,
        retrieval=[],
        conversation_state_tracker=tracker,
    )
    assert tracker.current_state == ConversationState.POST_CALL
