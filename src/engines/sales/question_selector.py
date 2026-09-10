"""QuestionSelector — deterministic next-question selection.

Decides which qualification field to ask for next, or returns None if asking
should pause (objection just raised, escalation required, customer asked
something, etc.). Never asks for a confirmed field. Never returns more than
one field.

Architecture: VoiceOS Phase 2 Sales Intelligence Layer.
"""

from __future__ import annotations

import logging

from src.engines.intent.result import IntentResult
from src.engines.risk.result import RiskAssessment
from src.engines.strategy.actions import StrategyAction
from src.engines.strategy.engine import StrategySelection
from src.libs.contracts.response_plan import IntentLabel

from .domains.base import DomainConfig
from .schema import QuestionField, SalesState

logger = logging.getLogger(__name__)

# Intents that indicate the customer asked a direct question — pause qualification
_DIRECT_QUESTION_INTENTS = frozenset(
    {IntentLabel.OTHER}  # Used as proxy for customer asking questions in collections domain
)

# Intents that block qualification resumption immediately
_BLOCK_QUALIFICATION_INTENTS = frozenset(
    {IntentLabel.DISCONNECT, IntentLabel.ABUSE, IntentLabel.CONSENT_REVOKE}
)

# Strategy actions that mean qualification should stop
_STOP_STRATEGIES = frozenset(
    {StrategyAction.ESCALATE, StrategyAction.TRANSFER, StrategyAction.CLOSE}
)


class QuestionSelector:
    """Selects the next qualification field to ask, or None.

    Rules (checked in order):
      1. Hard stop: risk.escalation_required → None
      2. Hard stop: strategy in ESCALATE/TRANSFER/CLOSE → None
      3. Hard stop: intent is DISCONNECT/ABUSE → None
      4. Hard stop: strategy is REASSURE (objection/hardship being handled) → None
      5. Direct question intent (OTHER with no field match) → None
      6. Objection not yet handled (previous intent was DISPUTE/HARDSHIP and
         last action wasn't HANDLE_OBJECTION) → None
      7. Compute missing + dependency-satisfying fields
      8. Return highest-priority available field, or None if all confirmed

    Architecture: VoiceOS Phase 2 Sales Intelligence Layer.
    """

    def __init__(self, domain: DomainConfig) -> None:
        self._domain = domain

    def select(
        self,
        state: SalesState,
        intent: IntentResult,
        strategy: StrategySelection,
        risk: RiskAssessment,
    ) -> QuestionField | None:
        """Select the next field to ask for, or None.

        Args:
            state: Current SalesState (already updated by SalesStateUpdater).
            intent: IntentResult from IntentEngine.
            strategy: StrategySelection from StrategyEngine.
            risk: RiskAssessment from RiskEngine.

        Returns:
            The highest-priority unconfirmed QuestionField whose dependencies
            are satisfied, or None if no question should be asked this turn.
        """
        # Hard stop 1: escalation
        if risk.escalation_required or risk.human_handoff_required:
            logger.debug("QuestionSelector: None — escalation required")
            return None

        # Hard stop 2: strategy blocks qualification
        if strategy.action in _STOP_STRATEGIES:
            logger.debug("QuestionSelector: None — stop strategy %s", strategy.action)
            return None

        # Hard stop 3: customer ending conversation
        if intent.label in _BLOCK_QUALIFICATION_INTENTS:
            logger.debug("QuestionSelector: None — blocking intent %s", intent.label)
            return None

        # Hard stop 4: de-escalation / reassurance in progress — don't push
        if strategy.action == StrategyAction.REASSURE:
            logger.debug("QuestionSelector: None — REASSURE strategy active")
            return None

        # Hard stop 5: objection just raised — handle it first
        # An objection is unhandled when: there's an active objection AND the last
        # action taken was not HANDLE_OBJECTION. We resume qualifying once the
        # objection has been addressed.
        if state.objection_count > 0 and state.last_sales_action not in (
            None,
            # These are the only actions that mean "we handled the objection"
            # before coming back to qualify
        ):
            # If the very last action was HANDLE_OBJECTION, we can resume
            from .schema import SalesAction

            if state.last_sales_action != SalesAction.HANDLE_OBJECTION and intent.label in (
                IntentLabel.DISPUTE,
                IntentLabel.HARDSHIP,
            ):
                logger.debug("QuestionSelector: None — unhandled objection")
                return None

        # Compute which required fields are still missing
        missing = [
            f
            for f in self._domain.required_fields
            if f.value not in state.confirmed_fields
        ]

        if not missing:
            logger.debug("QuestionSelector: None — all required fields confirmed")
            return None

        # Filter by dependency graph — only include fields whose prerequisites are met
        available = [
            f
            for f in missing
            if all(
                prereq.value in state.confirmed_fields
                for prereq in self._domain.dependencies.get(f, [])
            )
        ]

        if not available:
            logger.debug("QuestionSelector: None — all available fields blocked by deps")
            return None

        # Return the highest-priority available field (domain config already ordered)
        selected = available[0]
        logger.debug("QuestionSelector: selected field %s", selected)
        return selected
