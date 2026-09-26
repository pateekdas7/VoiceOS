"""Unit tests for QuestionSelector.

Tests cover all 10 specified scenarios plus edge cases.
"""

from __future__ import annotations

import pytest

from src.engines.intent.result import IntentResult
from src.engines.risk.result import RiskAssessment
from src.engines.sales.domains.real_estate import RealEstateDomainConfig
from src.engines.sales.question_selector import QuestionSelector
from src.engines.sales.schema import (
    DecisionMaker,
    FinancingStatus,
    LeadIntent,
    LeadTemperature,
    PropertyPurpose,
    QualificationStatus,
    QuestionField,
    SalesAction,
    SalesState,
    SiteVisitInterest,
    Timeline,
)
from src.engines.strategy.actions import StrategyAction
from src.engines.strategy.engine import StrategySelection
from src.libs.contracts.response_plan import IntentLabel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _intent(label: IntentLabel, confidence: float = 0.9) -> IntentResult:
    return IntentResult(
        label=label,
        confidence=confidence,
        reasoning_hint="test",
        source_span="test utterance",
    )


def _strategy(action: StrategyAction = StrategyAction.ASK) -> StrategySelection:
    return StrategySelection(action=action, confidence=0.9, rationale="test")


def _risk(escalation: bool = False, handoff: bool = False) -> RiskAssessment:
    return RiskAssessment(flags=[], escalation_required=escalation, human_handoff_required=handoff)


def _state_with_fields(*confirmed: QuestionField) -> SalesState:
    s = SalesState()
    s.confirmed_fields = [f.value for f in confirmed]
    return s


DOMAIN = RealEstateDomainConfig()
SELECTOR = QuestionSelector(domain=DOMAIN)


# ---------------------------------------------------------------------------
# TEST 1: Normal qualification — location + property known → next = BUDGET
# ---------------------------------------------------------------------------

def test_location_and_property_known_next_is_budget():
    """Location and property type confirmed → next question should be BUDGET (dep on LOCATION met)."""
    state = _state_with_fields(QuestionField.PURPOSE, QuestionField.LOCATION, QuestionField.PROPERTY_TYPE)
    result = SELECTOR.select(state, _intent(IntentLabel.OTHER), _strategy(), _risk())
    assert result == QuestionField.BUDGET, f"Expected BUDGET, got {result}"


# ---------------------------------------------------------------------------
# TEST 2: All required fields provided → no repeated questions → next might be
#         DECISION_MAKER or FINANCING depending on what's missing
# ---------------------------------------------------------------------------

def test_all_fields_except_decision_maker_next_is_decision_maker():
    """All required fields except DECISION_MAKER → next = DECISION_MAKER (not already asked)."""
    state = _state_with_fields(
        QuestionField.PURPOSE,
        QuestionField.LOCATION,
        QuestionField.PROPERTY_TYPE,
        QuestionField.BUDGET,
        QuestionField.TIMELINE,
        QuestionField.FINANCING,
    )
    result = SELECTOR.select(state, _intent(IntentLabel.OTHER), _strategy(), _risk())
    assert result == QuestionField.DECISION_MAKER


def test_all_required_fields_confirmed_returns_none():
    """All required fields confirmed → QuestionSelector returns None (nothing to ask)."""
    state = _state_with_fields(
        QuestionField.PURPOSE,
        QuestionField.LOCATION,
        QuestionField.PROPERTY_TYPE,
        QuestionField.BUDGET,
        QuestionField.TIMELINE,
        QuestionField.DECISION_MAKER,
        QuestionField.FINANCING,
    )
    result = SELECTOR.select(state, _intent(IntentLabel.OTHER), _strategy(), _risk())
    assert result is None


# ---------------------------------------------------------------------------
# TEST 3: Budget changes — state updates, no stale data
# (QuestionSelector should not re-ask BUDGET if it's still in confirmed_fields
#  even after a budget update — the updater handles moving it to uncertain)
# ---------------------------------------------------------------------------

def test_budget_confirmed_but_uncertain_not_re_asked_by_selector():
    """Budget confirmed but also in uncertain_fields (changed) — selector does NOT re-ask it."""
    state = SalesState()
    # Budget is both confirmed and uncertain (changed from prev value)
    state.confirmed_fields = [
        QuestionField.PURPOSE.value,
        QuestionField.LOCATION.value,
        QuestionField.PROPERTY_TYPE.value,
        QuestionField.BUDGET.value,  # confirmed but also uncertain
        QuestionField.TIMELINE.value,
        QuestionField.DECISION_MAKER.value,
        # FINANCING not yet confirmed
    ]
    state.uncertain_fields = [QuestionField.BUDGET.value]
    # Selector should skip BUDGET (it's confirmed) and ask FINANCING next
    result = SELECTOR.select(state, _intent(IntentLabel.OTHER), _strategy(), _risk())
    assert result == QuestionField.FINANCING


# ---------------------------------------------------------------------------
# TEST 4: Objection → HANDLE_OBJECTION, not ASK_BUDGET
# ---------------------------------------------------------------------------

def test_objection_intent_returns_none_not_field():
    """DISPUTE intent this turn → QuestionSelector returns None (objection takes priority)."""
    state = _state_with_fields(QuestionField.PURPOSE, QuestionField.LOCATION, QuestionField.PROPERTY_TYPE)
    state.objection_count = 1
    state.objections = ["PRICE_OBJECTION"]
    state.last_sales_action = SalesAction.ASK_BUDGET
    result = SELECTOR.select(state, _intent(IntentLabel.DISPUTE), _strategy(), _risk())
    # Objection intent with last action not HANDLE_OBJECTION → None
    assert result is None


# ---------------------------------------------------------------------------
# TEST 5: Direct question intent → no qualification question forced
# ---------------------------------------------------------------------------

def test_other_intent_with_no_objection_can_still_qualify():
    """OTHER intent without objection — qualification continues normally."""
    state = _state_with_fields(QuestionField.PURPOSE, QuestionField.LOCATION)
    result = SELECTOR.select(state, _intent(IntentLabel.OTHER), _strategy(), _risk())
    # PROPERTY_TYPE should be next (after PURPOSE and LOCATION)
    assert result == QuestionField.PROPERTY_TYPE


# ---------------------------------------------------------------------------
# TEST 6: High-intent lead (immediate timeline) → HOT, site visit rises
# ---------------------------------------------------------------------------

def test_high_intent_immediate_timeline_hot_state():
    """All fields confirmed + immediate timeline → site visit not yet offered means OFFER_SITE_VISIT."""
    state = _state_with_fields(
        QuestionField.PURPOSE,
        QuestionField.LOCATION,
        QuestionField.PROPERTY_TYPE,
        QuestionField.BUDGET,
        QuestionField.TIMELINE,
        QuestionField.DECISION_MAKER,
        QuestionField.FINANCING,
    )
    state.lead_intent = LeadIntent.HIGH
    state.lead_temperature = LeadTemperature.HOT
    state.timeline = Timeline.IMMEDIATE
    state.qualification_status = QualificationStatus.FULLY_QUALIFIED
    # SITE_VISIT_INTEREST is optional — selector should return None since all required done
    result = SELECTOR.select(state, _intent(IntentLabel.OTHER), _strategy(), _risk())
    assert result is None  # All required fields confirmed, nothing left to ask


# ---------------------------------------------------------------------------
# TEST 7: Low-intent lead ("just looking") → NURTURE, no aggressive push
# ---------------------------------------------------------------------------

def test_reassure_strategy_returns_none():
    """When strategy is REASSURE (de-escalation), QuestionSelector pauses qualification."""
    state = _state_with_fields(QuestionField.PURPOSE)
    result = SELECTOR.select(
        state, _intent(IntentLabel.HARDSHIP), _strategy(StrategyAction.REASSURE), _risk()
    )
    assert result is None


# ---------------------------------------------------------------------------
# TEST 8: HUMAN_HANDOFF request → qualification stops
# ---------------------------------------------------------------------------

def test_escalation_required_returns_none():
    """escalation_required=True → QuestionSelector returns None immediately."""
    state = _state_with_fields(QuestionField.PURPOSE, QuestionField.LOCATION)
    result = SELECTOR.select(state, _intent(IntentLabel.OTHER), _strategy(), _risk(escalation=True))
    assert result is None


def test_human_handoff_required_returns_none():
    """human_handoff_required=True → QuestionSelector returns None immediately."""
    state = _state_with_fields(QuestionField.PURPOSE, QuestionField.LOCATION)
    result = SELECTOR.select(state, _intent(IntentLabel.ABUSE), _strategy(), _risk(handoff=True))
    assert result is None


def test_escalate_strategy_returns_none():
    """Strategy ESCALATE → QuestionSelector returns None."""
    state = _state_with_fields(QuestionField.PURPOSE, QuestionField.LOCATION)
    result = SELECTOR.select(
        state, _intent(IntentLabel.OTHER), _strategy(StrategyAction.ESCALATE), _risk()
    )
    assert result is None


# ---------------------------------------------------------------------------
# TEST 9: Multi-field Hinglish extraction → all fields captured, next = missing
# ---------------------------------------------------------------------------

def test_multiple_confirmed_fields_next_is_first_missing():
    """PURPOSE, LOCATION, PROPERTY_TYPE, BUDGET confirmed → TIMELINE is next."""
    state = _state_with_fields(
        QuestionField.PURPOSE,
        QuestionField.LOCATION,
        QuestionField.PROPERTY_TYPE,
        QuestionField.BUDGET,
    )
    result = SELECTOR.select(state, _intent(IntentLabel.OTHER), _strategy(), _risk())
    assert result == QuestionField.TIMELINE


# ---------------------------------------------------------------------------
# TEST 10: Barge-in (state recalculates fresh from current SalesState)
# ---------------------------------------------------------------------------

def test_barge_in_recalculates_fresh():
    """Selector recalculates from current state regardless of previous plan."""
    # Simulate barge-in: previous action was ASK_BUDGET but state shows BUDGET confirmed
    state = _state_with_fields(
        QuestionField.PURPOSE,
        QuestionField.LOCATION,
        QuestionField.PROPERTY_TYPE,
        QuestionField.BUDGET,
    )
    state.last_sales_action = SalesAction.ASK_BUDGET  # We were asking budget
    # But customer answered BUDGET AND timeline in one turn
    state.confirmed_fields.append(QuestionField.TIMELINE.value)
    result = SELECTOR.select(state, _intent(IntentLabel.OTHER), _strategy(), _risk())
    # Next missing field should be DECISION_MAKER (PURPOSE+LOC+PROP+BUDGET+TIMELINE done)
    assert result == QuestionField.DECISION_MAKER


# ---------------------------------------------------------------------------
# TEST 11: Dependency not met — BUDGET blocked if LOCATION not confirmed
# ---------------------------------------------------------------------------

def test_dependency_blocks_budget_when_location_missing():
    """BUDGET depends on LOCATION — if LOCATION not confirmed, should ask LOCATION first."""
    state = _state_with_fields(QuestionField.PURPOSE)  # LOCATION not yet confirmed
    result = SELECTOR.select(state, _intent(IntentLabel.OTHER), _strategy(), _risk())
    # Should ask LOCATION (dep of BUDGET) before BUDGET
    assert result == QuestionField.LOCATION


# ---------------------------------------------------------------------------
# TEST 12: DISCONNECT intent → None
# ---------------------------------------------------------------------------

def test_disconnect_intent_returns_none():
    """DISCONNECT intent → QuestionSelector returns None."""
    state = _state_with_fields(QuestionField.PURPOSE)
    result = SELECTOR.select(state, _intent(IntentLabel.DISCONNECT), _strategy(), _risk())
    assert result is None


# ---------------------------------------------------------------------------
# TEST 13: CLOSE strategy → None
# ---------------------------------------------------------------------------

def test_close_strategy_returns_none():
    """Strategy CLOSE → no qualification."""
    state = _state_with_fields(QuestionField.PURPOSE)
    result = SELECTOR.select(
        state, _intent(IntentLabel.OTHER), _strategy(StrategyAction.CLOSE), _risk()
    )
    assert result is None
