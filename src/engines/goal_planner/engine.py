"""GoalPlanner — determines the primary goal for the current turn.

Selects exactly one Goal per turn from the bounded Goal set. Selection is
constrained by CustomerContext (outstanding balance, DPD) and the current
risk/state context. No LLM is called.

Selection logic (in priority order):
  1. TRANSFER_AGENT   — if human_handoff_required (abuse/escalation)
  2. END_CALL         — if consent revoked or call closing
  3. DE_ESCALATE      — if abuse/critical stress present
  4. VERIFY_IDENTITY  — if identity not yet verified
  5. HANDLE_DISPUTE   — if dispute claim raised
  6. COLLECT_FULL_PAYMENT  — if DPD ≤ threshold and full balance collectible
  7. COLLECT_PARTIAL_PAYMENT — if outstanding above partial threshold
  8. SECURE_PTP       — default when full/partial collection not viable this turn

Architecture: V2 Ch7 (Goal Planner).
"""

from __future__ import annotations

import logging

from src.libs.contracts.context import CustomerContext
from src.libs.contracts.response_plan import IntentLabel

from ..risk.flags import RiskFlag
from ..risk.result import RiskAssessment
from .goals import Goal

logger = logging.getLogger(__name__)

# DPD threshold below which full-payment collection is the primary goal.
_FULL_PAYMENT_DPD_THRESHOLD = 30

# Minimum outstanding (in minor units) to attempt partial payment collection.
_PARTIAL_PAYMENT_MIN_MINOR = 100_00  # ₹100


class GoalPlanner:
    """Determines the primary goal for the current agent turn.

    Returns exactly one Goal. No LLM calls — purely deterministic.

    Architecture: V2 Ch7.
    """

    def plan(
        self,
        context: CustomerContext | None,
        primary_intent: str,
        risk: RiskAssessment | None = None,
        conversation_state: str = "GREETING",
        identity_verified: bool = False,
    ) -> Goal:
        """Determine the primary goal for this turn.

        Args:
            context: Authoritative CustomerContext snapshot. May be None when
                     the CRM data is not yet available (pre-verification turns).
            primary_intent: Primary IntentLabel from the IntentEngine.
            risk: Risk assessment from the RiskEngine.
            conversation_state: Current ConversationState value (string).
            identity_verified: Whether identity has been confirmed this session.

        Returns:
            Exactly one Goal representing the agent's primary objective.
        """
        risk_flags: list[RiskFlag] = risk.flags if risk else []
        human_handoff_required = risk.human_handoff_required if risk else False

        goal = self._select(
            context,
            primary_intent,
            risk_flags,
            human_handoff_required,
            conversation_state,
            identity_verified,
        )

        logger.debug(
            "Goal planned",
            extra={
                "goal": goal.value,
                "primary_intent": primary_intent,
                "conversation_state": conversation_state,
                "risk_flags": [f.value for f in risk_flags],
            },
        )
        return goal

    def _select(
        self,
        context: CustomerContext | None,
        primary_intent: str,
        risk_flags: list[RiskFlag],
        human_handoff_required: bool,
        conversation_state: str,
        identity_verified: bool,
    ) -> Goal:
        """Apply goal selection logic in strict priority order."""

        # Priority 1: Human handoff required (abuse, extreme escalation)
        if human_handoff_required or RiskFlag.ABUSE_DETECTED in risk_flags:
            return Goal.TRANSFER_AGENT

        # Priority 2: Call ending signals
        if primary_intent in (IntentLabel.DISCONNECT, IntentLabel.CONSENT_REVOKE) or conversation_state in (
            "CLOSING",
            "POST_CALL",
        ):
            return Goal.END_CALL

        # Priority 3: De-escalation
        if RiskFlag.ESCALATION_TRIGGER in risk_flags and RiskFlag.ABUSE_DETECTED not in risk_flags:
            return Goal.DE_ESCALATE

        # Priority 4: Identity verification gate
        if not identity_verified and conversation_state in ("GREETING", "VERIFICATION"):
            return Goal.VERIFY_IDENTITY

        # Priority 5: Dispute handling
        if RiskFlag.DISPUTE_CLAIM in risk_flags or primary_intent == IntentLabel.DISPUTE:
            return Goal.HANDLE_DISPUTE

        # Priority 6 onward: Collection goals (require CustomerContext)
        if context is None:
            # Without context we cannot set a financial goal safely
            return Goal.VERIFY_IDENTITY

        outstanding = context.outstanding
        max_dpd = context.max_dpd

        if outstanding is not None:
            total_minor = outstanding.total_outstanding.amount_minor

            # Priority 6: Full payment — viable when DPD is low and full balance available
            if max_dpd <= _FULL_PAYMENT_DPD_THRESHOLD and total_minor > 0:
                return Goal.COLLECT_FULL_PAYMENT

            # Priority 7: Partial payment — when outstanding is significant
            if total_minor >= _PARTIAL_PAYMENT_MIN_MINOR:
                return Goal.COLLECT_PARTIAL_PAYMENT

        # Priority 8: Secure promise-to-pay (default collection goal)
        return Goal.SECURE_PTP
