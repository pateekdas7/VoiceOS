"""Unit tests for SalesStateUpdater.

Covers: budget updates, location expansion, qualification score, objection
tracking, field confidence tracking.
"""

from __future__ import annotations

import pytest

from src.engines.entity_extraction.result import ExtractedEntities
from src.engines.intent.result import IntentResult
from src.engines.risk.result import RiskAssessment
from src.engines.sales.domains.real_estate import RealEstateDomainConfig
from src.engines.sales.schema import (
    FinancingStatus,
    LeadIntent,
    PropertyPurpose,
    QualificationStatus,
    QuestionField,
    SalesState,
    Timeline,
)
from src.engines.sales.state_updater import SalesStateUpdater
from src.engines.strategy.actions import StrategyAction
from src.engines.strategy.engine import StrategySelection
from src.libs.contracts.response_plan import IntentLabel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DOMAIN = RealEstateDomainConfig()
UPDATER = SalesStateUpdater(domain=DOMAIN)


def _intent(label: IntentLabel, span: str = "test utterance") -> IntentResult:
    return IntentResult(
        label=label,
        confidence=0.9,
        reasoning_hint="test",
        source_span=span,
    )


def _risk(escalation: bool = False, handoff: bool = False) -> RiskAssessment:
    from src.engines.risk.flags import RiskFlag
    return RiskAssessment(flags=[], escalation_required=escalation, human_handoff_required=handoff)


def _strategy(action: StrategyAction = StrategyAction.ASK) -> StrategySelection:
    return StrategySelection(action=action, confidence=0.9, rationale="test")


def _entities() -> ExtractedEntities:
    return ExtractedEntities(slots={}, confidence=0.0)


def _update(
    previous: SalesState | None,
    span: str,
    intent_label: IntentLabel = IntentLabel.OTHER,
    conversation_state: str = "DEBT_DISCUSSION",
    risk_escalation: bool = False,
) -> SalesState:
    """Convenience: run SalesStateUpdater with a transcript span."""
    intent = _intent(intent_label, span)
    return UPDATER.update(
        previous_state=previous,
        entities=_entities(),
        intent=intent,
        conversation_state=conversation_state,
        strategy=_strategy(),
        risk=_risk(escalation=risk_escalation),
    )


# ---------------------------------------------------------------------------
# Budget extraction and update
# ---------------------------------------------------------------------------

def test_budget_extraction_lakh():
    """'80 lakh' extracts to budget_min=8000000 and budget_max=8000000."""
    state = _update(None, "Budget around 80 lakh hai.")
    assert state.budget_min == 8_000_000
    assert state.budget_max == 8_000_000
    assert QuestionField.BUDGET.value in state.confirmed_fields


def test_budget_change_flagged_as_uncertain():
    """When budget changes, it remains confirmed but is also added to uncertain_fields."""
    state1 = _update(None, "Budget 80 lakh hai.")
    assert QuestionField.BUDGET.value in state1.confirmed_fields

    # Customer changes budget to 85 lakh
    state2 = _update(state1, "Theek hai, 85 lakh tak stretch kar sakta hoon.")
    # Budget should be updated to new value
    assert state2.budget_max == 8_500_000
    # Budget stays confirmed (new value was explicitly stated)
    assert QuestionField.BUDGET.value in state2.confirmed_fields
    # But also flagged as uncertain (changed from previous)
    assert QuestionField.BUDGET.value in state2.uncertain_fields


def test_budget_range_extraction():
    """'80 se 85 lakh' extracts a range."""
    state = _update(None, "Budget 80 se 85 lakh hai.")
    assert state.budget_min == 8_000_000
    assert state.budget_max == 8_500_000


# ---------------------------------------------------------------------------
# Location expansion
# ---------------------------------------------------------------------------

def test_location_extraction():
    """'Noida Extension' extracted to location list."""
    state = _update(None, "Main Noida Extension mein 3 BHK dekh raha hoon.")
    assert "Noida Extension" in state.location
    assert QuestionField.LOCATION.value in state.confirmed_fields


def test_location_expansion_adds_new():
    """Adding Gurgaon to existing Noida — both present in state.location."""
    state1 = _update(None, "Noida mein dekhna hai.")
    assert "Noida" in state1.location

    state2 = _update(state1, "Gurgaon bhi dekh sakte hain.")
    assert "Noida" in state2.location
    assert "Gurugram" in state2.location or "Gurgaon" in state2.location


# ---------------------------------------------------------------------------
# Property type extraction
# ---------------------------------------------------------------------------

def test_property_type_extraction():
    """'3 BHK' extracted correctly."""
    state = _update(None, "3BHK chahiye.")
    assert state.property_type is not None
    assert "3BHK" in state.property_type
    assert QuestionField.PROPERTY_TYPE.value in state.confirmed_fields


# ---------------------------------------------------------------------------
# Purpose extraction
# ---------------------------------------------------------------------------

def test_purpose_investment():
    """'investment ke liye' → PropertyPurpose.INVESTMENT."""
    state = _update(None, "Investment ke liye chahiye.")
    assert state.purpose == PropertyPurpose.INVESTMENT
    assert QuestionField.PURPOSE.value in state.confirmed_fields


def test_purpose_self_use():
    """'rehne ke liye' → PropertyPurpose.SELF_USE."""
    state = _update(None, "Khud rehne ke liye chahiye.")
    assert state.purpose == PropertyPurpose.SELF_USE


# ---------------------------------------------------------------------------
# Timeline extraction
# ---------------------------------------------------------------------------

def test_timeline_immediate():
    """'abhi' → Timeline.IMMEDIATE."""
    state = _update(None, "Abhi lena chahta hoon.")
    assert state.timeline == Timeline.IMMEDIATE


def test_timeline_2_months():
    """'agle 2 mahine' → Timeline.MONTHS_1_2."""
    state = _update(None, "Agle do mahine mein chahiye.")
    assert state.timeline == Timeline.MONTHS_1_2


def test_timeline_exploring():
    """'bas dekhna hai' → Timeline.EXPLORING."""
    state = _update(None, "Bas dekhna hai abhi.")
    assert state.timeline == Timeline.EXPLORING


# ---------------------------------------------------------------------------
# Qualification score
# ---------------------------------------------------------------------------

def test_score_increases_with_fields():
    """Score increases as more fields are confirmed."""
    state1 = _update(None, "Main Noida Extension mein dekhna chahta hoon.")
    state2 = _update(state1, "3BHK chahiye.")
    state3 = _update(state2, "Budget 80 lakh hai.")
    assert state3.qualification_score > state2.qualification_score > state1.qualification_score


def test_score_bounded_0_to_100():
    """Score is always in [0, 100] regardless of inputs."""
    state = _update(None, "Main Noida mein 3BHK chahiye, budget 80 lakh, investment ke liye, agle do mahine mein.")
    assert 0 <= state.qualification_score <= 100


def test_score_decreases_with_objections():
    """Objections reduce the score (each objection -5)."""
    from src.engines.risk.flags import RiskFlag
    state_no_obj = _update(None, "Noida mein 3BHK, 80 lakh.")
    # Add objection manually to test score deduction
    state_no_obj.objection_count = 0
    # Re-update score
    from src.engines.sales.state_updater import _compute_qualification_score
    score_no_obj, _ = _compute_qualification_score(state_no_obj, DOMAIN)

    state_with_obj = _update(None, "Noida mein 3BHK, 80 lakh.")
    state_with_obj.objection_count = 2
    state_with_obj.objections = ["PRICE_OBJECTION", "BUDGET_CONCERN"]
    score_with_obj, _ = _compute_qualification_score(state_with_obj, DOMAIN)

    assert score_with_obj < score_no_obj


# ---------------------------------------------------------------------------
# Objection tracking
# ---------------------------------------------------------------------------

def test_dispute_intent_adds_objection():
    """DISPUTE intent adds PRICE_OBJECTION to objections list."""
    state = _update(None, "Price bahut high lag raha hai.", intent_label=IntentLabel.DISPUTE)
    assert "PRICE_OBJECTION" in state.objections
    assert state.objection_count == 1


def test_hardship_intent_adds_objection():
    """HARDSHIP intent adds BUDGET_CONCERN."""
    state = _update(None, "Abhi thodi problem hai.", intent_label=IntentLabel.HARDSHIP)
    assert "BUDGET_CONCERN" in state.objections


def test_objection_not_duplicated():
    """Same objection label not added twice."""
    state1 = _update(None, "High price hai.", intent_label=IntentLabel.DISPUTE)
    state2 = _update(state1, "Baat suno, price zyada hai.", intent_label=IntentLabel.DISPUTE)
    assert state2.objections.count("PRICE_OBJECTION") == 1


# ---------------------------------------------------------------------------
# Decision maker extraction
# ---------------------------------------------------------------------------

def test_decision_maker_self():
    """'main akela decide' → DecisionMaker.SELF."""
    from src.engines.sales.schema import DecisionMaker
    state = _update(None, "Main akela decide kar sakta hoon.")
    assert state.decision_maker == DecisionMaker.SELF
    assert QuestionField.DECISION_MAKER.value in state.confirmed_fields


# ---------------------------------------------------------------------------
# Financing extraction
# ---------------------------------------------------------------------------

def test_financing_home_loan():
    """'home loan se' → FinancingStatus.HOME_LOAN."""
    state = _update(None, "Home loan se lena hai.")
    assert state.financing_status == FinancingStatus.HOME_LOAN
    assert QuestionField.FINANCING.value in state.confirmed_fields


# ---------------------------------------------------------------------------
# Field tracking
# ---------------------------------------------------------------------------

def test_unanswered_required_fields_decreases():
    """unanswered_required_fields decreases as fields get confirmed."""
    state1 = _update(None, "Noida mein dekhna hai.")
    count1 = len(state1.unanswered_required_fields)

    state2 = _update(state1, "3BHK chahiye.")
    count2 = len(state2.unanswered_required_fields)

    assert count2 < count1


def test_qualification_progress_increases():
    """qualification_progress increases from 0 to 1 as fields confirmed."""
    state0 = SalesState()
    assert state0.qualification_progress == 0.0

    state1 = _update(None, "Noida mein 3BHK.")
    assert state1.qualification_progress > 0.0

    state2 = _update(state1, "Budget 80 lakh, investment ke liye, agle do mahine mein, main khud decide karta hoon, home loan se.")
    assert state2.qualification_progress <= 1.0
    assert state2.qualification_progress > state1.qualification_progress


# ---------------------------------------------------------------------------
# Lead intent
# ---------------------------------------------------------------------------

def test_promise_to_pay_gives_high_intent():
    """PROMISE_TO_PAY intent → HIGH lead intent."""
    state = _update(None, "Haan, main le lunga.", intent_label=IntentLabel.PROMISE_TO_PAY)
    assert state.lead_intent == LeadIntent.HIGH


def test_disconnect_gives_low_intent():
    """DISCONNECT → LOW lead intent."""
    state = _update(None, "Mujhe baat nahi karni.", intent_label=IntentLabel.DISCONNECT)
    assert state.lead_intent == LeadIntent.LOW
