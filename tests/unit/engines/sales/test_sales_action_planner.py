"""Unit tests for SalesActionPlanner.

Covers: risk override, objection handling priority, field→action mapping.
"""

from __future__ import annotations

import pytest

from src.engines.goal_planner.goals import Goal
from src.engines.intent.result import IntentResult
from src.engines.risk.result import RiskAssessment
from src.engines.sales.action_planner import SalesActionPlanner
from src.engines.sales.domains.real_estate import RealEstateDomainConfig
from src.engines.sales.schema import (
    LeadIntent,
    LeadStage,
    QualificationStatus,
    QuestionField,
    SalesAction,
    SalesState,
    SiteVisitInterest,
)
from src.engines.strategy.actions import StrategyAction
from src.engines.strategy.engine import StrategySelection
from src.libs.contracts.response_plan import IntentLabel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DOMAIN = RealEstateDomainConfig()
PLANNER = SalesActionPlanner(domain=DOMAIN)


def _intent(label: IntentLabel, span: str = "test") -> IntentResult:
    return IntentResult(label=label, confidence=0.9, reasoning_hint="test", source_span=span)


def _risk(escalation: bool = False, handoff: bool = False) -> RiskAssessment:
    return RiskAssessment(flags=[], escalation_required=escalation, human_handoff_required=handoff)


def _strategy(action: StrategyAction = StrategyAction.ASK) -> StrategySelection:
    return StrategySelection(action=action, confidence=0.9, rationale="test")


def _plan(
    state: SalesState,
    intent: IntentResult | None = None,
    strategy: StrategySelection | None = None,
    risk: RiskAssessment | None = None,
    goal: Goal = Goal.SECURE_PTP,
    next_question: QuestionField | None = None,
) -> SalesAction:
    return PLANNER.plan(
        state=state,
        intent=intent or _intent(IntentLabel.OTHER),
        strategy=strategy or _strategy(),
        risk=risk or _risk(),
        goal=goal,
        next_question=next_question,
    )


# ---------------------------------------------------------------------------
# Priority 1: Risk override → HUMAN_HANDOFF always wins
# ---------------------------------------------------------------------------

def test_risk_escalation_overrides_everything():
    """human_handoff_required=True → HUMAN_HANDOFF regardless of other signals."""
    state = SalesState()
    action = _plan(state, risk=_risk(handoff=True), next_question=QuestionField.BUDGET)
    assert action == SalesAction.HUMAN_HANDOFF


def test_escalation_required_overrides_everything():
    """escalation_required=True → HUMAN_HANDOFF."""
    state = SalesState()
    action = _plan(state, risk=_risk(escalation=True))
    assert action == SalesAction.HUMAN_HANDOFF


# ---------------------------------------------------------------------------
# Priority 2: ABUSE → END_CONVERSATION
# ---------------------------------------------------------------------------

def test_abuse_intent_ends_conversation():
    """ABUSE intent → END_CONVERSATION (priority 2, overrides objection handling)."""
    state = SalesState()
    action = _plan(state, intent=_intent(IntentLabel.ABUSE))
    assert action == SalesAction.END_CONVERSATION


# ---------------------------------------------------------------------------
# Priority 3: DISCONNECT → END_CONVERSATION
# ---------------------------------------------------------------------------

def test_disconnect_intent_ends_conversation():
    """DISCONNECT → END_CONVERSATION."""
    state = SalesState()
    action = _plan(state, intent=_intent(IntentLabel.DISCONNECT))
    assert action == SalesAction.END_CONVERSATION


# ---------------------------------------------------------------------------
# Priority 4: Explicit objection before any qualification action
# ---------------------------------------------------------------------------

def test_dispute_intent_gives_handle_objection():
    """DISPUTE intent this turn → HANDLE_OBJECTION (not ASK_BUDGET)."""
    state = SalesState()
    state.confirmed_fields = [
        QuestionField.PURPOSE.value,
        QuestionField.LOCATION.value,
        QuestionField.PROPERTY_TYPE.value,
    ]
    action = _plan(
        state,
        intent=_intent(IntentLabel.DISPUTE),
        next_question=QuestionField.BUDGET,
    )
    assert action == SalesAction.HANDLE_OBJECTION


def test_hardship_intent_gives_handle_objection():
    """HARDSHIP intent → HANDLE_OBJECTION."""
    state = SalesState()
    action = _plan(state, intent=_intent(IntentLabel.HARDSHIP))
    assert action == SalesAction.HANDLE_OBJECTION


# ---------------------------------------------------------------------------
# Priority 5: Strategy ESCALATE/TRANSFER → HUMAN_HANDOFF
# ---------------------------------------------------------------------------

def test_escalate_strategy_gives_human_handoff():
    """Strategy ESCALATE → HUMAN_HANDOFF (after abuse/disconnect/objection checks)."""
    state = SalesState()
    action = _plan(state, strategy=_strategy(StrategyAction.ESCALATE))
    assert action == SalesAction.HUMAN_HANDOFF


def test_transfer_strategy_gives_human_handoff():
    """Strategy TRANSFER → HUMAN_HANDOFF."""
    state = SalesState()
    action = _plan(state, strategy=_strategy(StrategyAction.TRANSFER))
    assert action == SalesAction.HUMAN_HANDOFF


# ---------------------------------------------------------------------------
# Priority 6: CALLBACK → SCHEDULE_FOLLOWUP
# ---------------------------------------------------------------------------

def test_callback_intent_schedules_followup():
    """CALLBACK intent → SCHEDULE_FOLLOWUP."""
    state = SalesState()
    action = _plan(state, intent=_intent(IntentLabel.CALLBACK))
    assert action == SalesAction.SCHEDULE_FOLLOWUP


# ---------------------------------------------------------------------------
# Priority 7+8: Site visit actions
# ---------------------------------------------------------------------------

def test_fully_qualified_site_visit_confirmed():
    """All fields confirmed + site_visit==CONFIRMED → CONFIRM_SITE_VISIT."""
    state = SalesState()
    state.confirmed_fields = [f.value for f in DOMAIN.required_fields]
    state.qualification_status = QualificationStatus.FULLY_QUALIFIED
    state.lead_intent = LeadIntent.HIGH
    state.site_visit_interest = SiteVisitInterest.CONFIRMED
    action = _plan(state, next_question=None)
    assert action == SalesAction.CONFIRM_SITE_VISIT


def test_fully_qualified_high_intent_offers_site_visit():
    """All fields confirmed + HIGH intent + site not offered → OFFER_SITE_VISIT."""
    state = SalesState()
    state.confirmed_fields = [f.value for f in DOMAIN.required_fields]
    state.qualification_status = QualificationStatus.FULLY_QUALIFIED
    state.lead_intent = LeadIntent.HIGH
    state.site_visit_interest = None  # Not yet offered
    action = _plan(state, next_question=None)
    assert action == SalesAction.OFFER_SITE_VISIT


# ---------------------------------------------------------------------------
# Priority 9: next_question → maps to correct ASK_* action
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field,expected_action", [
    (QuestionField.PURPOSE, SalesAction.ASK_PURPOSE),
    (QuestionField.LOCATION, SalesAction.ASK_LOCATION),
    (QuestionField.PROPERTY_TYPE, SalesAction.ASK_PROPERTY_TYPE),
    (QuestionField.BUDGET, SalesAction.ASK_BUDGET),
    (QuestionField.TIMELINE, SalesAction.ASK_TIMELINE),
    (QuestionField.DECISION_MAKER, SalesAction.ASK_DECISION_MAKER),
    (QuestionField.FINANCING, SalesAction.ASK_FINANCING),
    (QuestionField.SITE_VISIT_INTEREST, SalesAction.OFFER_SITE_VISIT),
])
def test_field_maps_to_correct_ask_action(field: QuestionField, expected_action: SalesAction):
    """QuestionField → SalesAction mapping is correct."""
    state = SalesState()
    action = _plan(state, next_question=field)
    assert action == expected_action, f"Field {field} should map to {expected_action}, got {action}"


# ---------------------------------------------------------------------------
# Default cases
# ---------------------------------------------------------------------------

def test_greeting_stage_returns_greet():
    """NEW stage with no other signal → GREET."""
    state = SalesState()
    state.lead_stage = LeadStage.NEW
    action = _plan(state)
    assert action == SalesAction.GREET


def test_default_action_is_qualify():
    """Engaged lead with no special signals → QUALIFY."""
    state = SalesState()
    state.lead_stage = LeadStage.ENGAGED
    action = _plan(state)
    assert action == SalesAction.QUALIFY


# ---------------------------------------------------------------------------
# Human handoff intent
# ---------------------------------------------------------------------------

def test_disconnect_before_escalate_strategy():
    """DISCONNECT intent triggers END_CONVERSATION before strategy ESCALATE check."""
    state = SalesState()
    action = _plan(
        state,
        intent=_intent(IntentLabel.DISCONNECT),
        strategy=_strategy(StrategyAction.ESCALATE),
    )
    # DISCONNECT (priority 3) comes before strategy check (priority 5)
    assert action == SalesAction.END_CONVERSATION
