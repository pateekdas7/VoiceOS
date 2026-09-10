"""SalesActionPlanner — deterministic single-action selection.

Produces exactly one SalesAction per turn from the priority rules below.
No LLM calls. No randomness. Same inputs → same output.

Priority order (highest first):
  1. Risk escalation required → HUMAN_HANDOFF
  2. ABUSE intent → END_CONVERSATION
  3. DISCONNECT/CONSENT_REVOKE intent → END_CONVERSATION
  4. Explicit objection (DISPUTE/HARDSHIP) → HANDLE_OBJECTION
  5. Strategy ESCALATE/TRANSFER → HUMAN_HANDOFF
  6. Existing commitment to follow up (CALLBACK intent) → SCHEDULE_FOLLOWUP
  7. All required fields known + site_visit==CONFIRMED → CONFIRM_SITE_VISIT
  8. All required fields known + intent HIGH + site visit not yet offered → OFFER_SITE_VISIT
  9. next_question is not None → ASK_{FIELD}
  10. Most required fields known but uncertain → CONFIRM_REQUIREMENT
  11. Low intent, very early → NURTURE
  12. GREETING state → GREET
  13. Default → QUALIFY

Architecture: VoiceOS Phase 2 Sales Intelligence Layer.
"""

from __future__ import annotations

import logging

from src.engines.goal_planner.goals import Goal
from src.engines.intent.result import IntentResult
from src.engines.risk.result import RiskAssessment
from src.engines.strategy.actions import StrategyAction
from src.engines.strategy.engine import StrategySelection
from src.libs.contracts.response_plan import IntentLabel

from .domains.base import DomainConfig
from .schema import (
    LeadIntent,
    LeadStage,
    QualificationStatus,
    QuestionField,
    SalesAction,
    SalesState,
    SiteVisitInterest,
)

logger = logging.getLogger(__name__)

# Map QuestionField → SalesAction
_FIELD_TO_ACTION: dict[QuestionField, SalesAction] = {
    QuestionField.PURPOSE: SalesAction.ASK_PURPOSE,
    QuestionField.LOCATION: SalesAction.ASK_LOCATION,
    QuestionField.PROPERTY_TYPE: SalesAction.ASK_PROPERTY_TYPE,
    QuestionField.BUDGET: SalesAction.ASK_BUDGET,
    QuestionField.TIMELINE: SalesAction.ASK_TIMELINE,
    QuestionField.DECISION_MAKER: SalesAction.ASK_DECISION_MAKER,
    QuestionField.FINANCING: SalesAction.ASK_FINANCING,
    QuestionField.PREFERRED_LOCALITY: SalesAction.ASK_LOCATION,  # same action, different field
    QuestionField.SITE_VISIT_INTEREST: SalesAction.OFFER_SITE_VISIT,
    QuestionField.COMPETITOR_CONSIDERATION: SalesAction.QUALIFY,
}


class SalesActionPlanner:
    """Selects the single most appropriate SalesAction for this turn.

    All logic is deterministic — zero LLM calls, zero I/O.

    Architecture: VoiceOS Phase 2 Sales Intelligence Layer.
    """

    def __init__(self, domain: DomainConfig) -> None:
        self._domain = domain

    def plan(
        self,
        state: SalesState,
        intent: IntentResult,
        strategy: StrategySelection,
        risk: RiskAssessment,
        goal: Goal,
        next_question: QuestionField | None,
    ) -> SalesAction:
        """Select the best SalesAction for this turn.

        Args:
            state: Current SalesState (after SalesStateUpdater).
            intent: IntentResult from IntentEngine.
            strategy: StrategySelection from StrategyEngine.
            risk: RiskAssessment from RiskEngine.
            goal: Goal from GoalPlanner.
            next_question: The field QuestionSelector recommends asking, or None.

        Returns:
            Exactly one SalesAction.
        """
        action = self._resolve(state, intent, strategy, risk, goal, next_question)
        logger.debug(
            "SalesActionPlanner: action=%s intent=%s strategy=%s",
            action.value,
            intent.label.value,
            strategy.action.value,
        )
        return action

    def _resolve(
        self,
        state: SalesState,
        intent: IntentResult,
        strategy: StrategySelection,
        risk: RiskAssessment,
        goal: Goal,
        next_question: QuestionField | None,
    ) -> SalesAction:
        """Apply the priority rules and return the chosen action."""

        # Priority 1: Risk/policy escalation always wins
        if risk.human_handoff_required or risk.escalation_required:
            return SalesAction.HUMAN_HANDOFF

        # Priority 2: Abuse — end conversation immediately
        if intent.label == IntentLabel.ABUSE:
            return SalesAction.END_CONVERSATION

        # Priority 3: Customer ending conversation
        if intent.label in (IntentLabel.DISCONNECT, IntentLabel.CONSENT_REVOKE):
            return SalesAction.END_CONVERSATION

        # Priority 4: Explicit objection being raised this turn
        if intent.label in (IntentLabel.DISPUTE, IntentLabel.HARDSHIP):
            return SalesAction.HANDLE_OBJECTION

        # Priority 5: Strategy says escalate/transfer
        if strategy.action in (StrategyAction.ESCALATE, StrategyAction.TRANSFER):
            return SalesAction.HUMAN_HANDOFF

        # Priority 6: Customer wants callback / follow-up
        if intent.label == IntentLabel.CALLBACK:
            return SalesAction.SCHEDULE_FOLLOWUP

        # Priority 7: Site visit confirmed — confirm it
        if (
            state.qualification_status == QualificationStatus.FULLY_QUALIFIED
            and state.site_visit_interest == SiteVisitInterest.CONFIRMED
        ):
            return SalesAction.CONFIRM_SITE_VISIT

        # Priority 8: All fields known, high intent, site visit not yet offered
        if (
            state.qualification_status == QualificationStatus.FULLY_QUALIFIED
            and state.lead_intent == LeadIntent.HIGH
            and state.site_visit_interest not in (
                SiteVisitInterest.CONFIRMED,
                SiteVisitInterest.NOT_INTERESTED,
            )
        ):
            return SalesAction.OFFER_SITE_VISIT

        # Priority 9: QuestionSelector provided a field to ask
        if next_question is not None:
            return _FIELD_TO_ACTION.get(next_question, SalesAction.QUALIFY)

        # Priority 10: Some fields uncertain — ask for confirmation
        if state.uncertain_fields and state.qualification_progress > 0.5:
            return SalesAction.CONFIRM_REQUIREMENT

        # Priority 11: Low intent, early stage — don't push hard
        if state.lead_intent == LeadIntent.LOW and state.lead_stage in (
            LeadStage.NEW,
            LeadStage.ENGAGED,
        ):
            return SalesAction.NURTURE

        # Priority 12: Greeting state
        if state.lead_stage == LeadStage.NEW:
            return SalesAction.GREET

        # Priority 13: Default — continue qualifying
        return SalesAction.QUALIFY
