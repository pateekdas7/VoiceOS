"""Integration test: 10-turn simulated sales conversation.

Runs SalesStateUpdater + QuestionSelector + SalesActionPlanner for 10 turns
with mock inputs (no real LLM/STT/TTS). Verifies:
  - No field repeated after confirmation
  - Budget updates correctly
  - Objection handled before resuming
  - HUMAN_HANDOFF at Turn 10

Architecture: VoiceOS Phase 2 Sales Intelligence Layer.
"""

from __future__ import annotations

import pytest

from src.engines.entity_extraction.result import ExtractedEntities
from src.engines.goal_planner.goals import Goal
from src.engines.intent.result import IntentResult
from src.engines.risk.result import RiskAssessment
from src.engines.sales.action_planner import SalesActionPlanner
from src.engines.sales.domains.real_estate import RealEstateDomainConfig
from src.engines.sales.question_selector import QuestionSelector
from src.engines.sales.schema import (
    QuestionField,
    SalesAction,
    SalesState,
)
from src.engines.sales.state_updater import SalesStateUpdater
from src.engines.strategy.actions import StrategyAction
from src.engines.strategy.engine import StrategySelection
from src.libs.contracts.response_plan import IntentLabel
from src.engines.risk.flags import RiskFlag


# ---------------------------------------------------------------------------
# Test fixture: 10-turn conversation
# ---------------------------------------------------------------------------

DOMAIN = RealEstateDomainConfig()
UPDATER = SalesStateUpdater(domain=DOMAIN)
SELECTOR = QuestionSelector(domain=DOMAIN)
PLANNER = SalesActionPlanner(domain=DOMAIN)


def _intent(label: IntentLabel, span: str) -> IntentResult:
    return IntentResult(label=label, confidence=0.9, reasoning_hint="test", source_span=span)


def _risk_clean() -> RiskAssessment:
    return RiskAssessment(flags=[], escalation_required=False, human_handoff_required=False)


def _risk_escalation() -> RiskAssessment:
    return RiskAssessment(
        flags=[RiskFlag.ESCALATION_TRIGGER],
        escalation_required=True,
        human_handoff_required=False,
    )


def _strategy(action: StrategyAction = StrategyAction.ASK) -> StrategySelection:
    return StrategySelection(action=action, confidence=0.9, rationale="test")


# 10-turn conversation spec from the task
TURNS = [
    # (utterance, intent_label, risk_fn, strategy_action)
    ("Main Noida Extension mein 3 BHK dekh raha hoon.", IntentLabel.OTHER, _risk_clean, StrategyAction.ASK),
    ("Budget around 80 lakh hai.", IntentLabel.OTHER, _risk_clean, StrategyAction.ASK),
    ("Investment ke liye chahiye.", IntentLabel.OTHER, _risk_clean, StrategyAction.ASK),
    ("Price bahut high lag raha hai.", IntentLabel.DISPUTE, _risk_clean, StrategyAction.VERIFY),
    ("Theek hai, 85 lakh tak stretch kar sakta hoon.", IntentLabel.OTHER, _risk_clean, StrategyAction.ASK),
    ("Agle 2 mahine mein chahiye.", IntentLabel.OTHER, _risk_clean, StrategyAction.ASK),
    ("Main akela decide kar sakta hoon.", IntentLabel.OTHER, _risk_clean, StrategyAction.ASK),
    ("Home loan se lena hai.", IntentLabel.OTHER, _risk_clean, StrategyAction.ASK),
    ("Kya site visit ho sakta hai?", IntentLabel.OTHER, _risk_clean, StrategyAction.ASK),
    ("Mujhe kisi senior se baat karni hai.", IntentLabel.OTHER, _risk_escalation, StrategyAction.ESCALATE),
]


def run_10_turns():
    """Execute 10 turns and return (states, next_questions, actions) per turn."""
    previous_state: SalesState | None = None
    states = []
    questions = []
    actions = []
    asked_fields: list[str] = []  # fields we've asked, to verify no repetition

    for turn_num, (utterance, intent_label, risk_fn, strategy_action) in enumerate(TURNS):
        intent = _intent(intent_label, utterance)
        risk = risk_fn()
        strategy = _strategy(strategy_action)

        state = UPDATER.update(
            previous_state=previous_state,
            entities=ExtractedEntities(slots={}, confidence=0.0),
            intent=intent,
            conversation_state="DEBT_DISCUSSION",
            strategy=strategy,
            risk=risk,
        )
        next_q = SELECTOR.select(state, intent, strategy, risk)
        action = PLANNER.plan(state, intent, strategy, risk, Goal.SECURE_PTP, next_q)

        state.next_action = action
        state.next_question = next_q

        states.append(state)
        questions.append(next_q)
        actions.append(action)

        # Track which fields were asked (for repetition check)
        if next_q and next_q.value not in asked_fields:
            asked_fields.append(next_q.value)

        previous_state = state

    return states, questions, actions, asked_fields


# ---------------------------------------------------------------------------
# Actual test
# ---------------------------------------------------------------------------

class TestTenTurnConversation:
    """Run the 10-turn conversation and verify invariants."""

    def setup_method(self):
        self.states, self.questions, self.actions, self.asked_fields = run_10_turns()

    def test_no_confirmed_field_re_asked(self):
        """Once a field is confirmed, it is never asked again in subsequent turns."""
        previous_confirmed: set[str] = set()
        for i, (state, question) in enumerate(zip(self.states, self.questions)):
            if question is not None and question.value in previous_confirmed:
                raise AssertionError(
                    f"Turn {i+1}: field {question.value} was asked again after being confirmed. "
                    f"Previously confirmed: {previous_confirmed}"
                )
            previous_confirmed = set(state.confirmed_fields)

    def test_turn1_location_confirmed(self):
        """Turn 1: Noida Extension location extracted."""
        state = self.states[0]
        assert "Noida Extension" in state.location
        assert QuestionField.LOCATION.value in state.confirmed_fields

    def test_turn2_budget_80_lakh(self):
        """Turn 2: budget extracted as 80 lakh."""
        state = self.states[1]
        assert state.budget_max == 8_000_000

    def test_turn3_purpose_investment(self):
        """Turn 3: purpose extracted as INVESTMENT."""
        from src.engines.sales.schema import PropertyPurpose
        state = self.states[2]
        assert state.purpose == PropertyPurpose.INVESTMENT

    def test_turn4_objection_handled(self):
        """Turn 4: DISPUTE intent → action is HANDLE_OBJECTION (not ASK_*)."""
        action = self.actions[3]
        assert action == SalesAction.HANDLE_OBJECTION

    def test_turn5_budget_updated_to_85_lakh(self):
        """Turn 5: budget updated from 80L to 85L."""
        state = self.states[4]
        assert state.budget_max == 8_500_000

    def test_turn6_timeline_extracted(self):
        """Turn 6: '2 mahine' → MONTHS_1_2."""
        from src.engines.sales.schema import Timeline
        state = self.states[5]
        assert state.timeline == Timeline.MONTHS_1_2

    def test_turn7_decision_maker_self(self):
        """Turn 7: 'main akela decide' → DecisionMaker.SELF."""
        from src.engines.sales.schema import DecisionMaker
        state = self.states[6]
        assert state.decision_maker == DecisionMaker.SELF

    def test_turn8_financing_home_loan(self):
        """Turn 8: 'home loan se' → HOME_LOAN."""
        from src.engines.sales.schema import FinancingStatus
        state = self.states[7]
        assert state.financing_status == FinancingStatus.HOME_LOAN

    def test_turn9_site_visit_interest(self):
        """Turn 9: site visit mentioned — interest captured or site visit offered."""
        state = self.states[8]
        action = self.actions[8]
        # Either we offered site visit or we detected interest in the state
        assert (
            action in (SalesAction.OFFER_SITE_VISIT, SalesAction.CONFIRM_SITE_VISIT)
            or state.site_visit_interest is not None
            or QuestionField.SITE_VISIT_INTEREST.value in state.confirmed_fields
        )

    def test_turn10_human_handoff(self):
        """Turn 10: 'senior se baat' with ESCALATION_TRIGGER → HUMAN_HANDOFF."""
        action = self.actions[9]
        assert action == SalesAction.HUMAN_HANDOFF, f"Expected HUMAN_HANDOFF at turn 10, got {action}"

    def test_score_increases_over_turns(self):
        """Qualification score generally increases as fields are confirmed."""
        scores = [s.qualification_score for s in self.states]
        # Score at turn 6 should be higher than at turn 1
        assert scores[5] > scores[0], f"Scores did not increase: {scores}"

    def test_all_required_fields_eventually_confirmed(self):
        """By turn 8, all required fields should be in confirmed list."""
        state_t8 = self.states[7]
        for f in DOMAIN.required_fields:
            assert f.value in state_t8.confirmed_fields, (
                f"Required field {f.value} not confirmed by turn 8. "
                f"confirmed={state_t8.confirmed_fields}"
            )
