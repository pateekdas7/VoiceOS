"""StrategyEngine — deterministic constrained action selection.

Selects the next conversational action from the bounded StrategyAction space.
Selection is a lookup table + scoring function over (intent, conversation_state,
risk_flags, emotion). No LLM is called at any point.

Decision precedence (from V2 Ch4.14):
  de-escalate > comply > verify > collect

Architecture: V2 Ch4 (Strategy Engine).
"""

from __future__ import annotations

import logging

from src.libs.contracts.response_plan import IntentLabel
from src.libs.contracts.streaming import StressLevel

from ..risk.flags import RiskFlag
from ..risk.result import RiskAssessment
from .actions import StrategyAction

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

_INTENT_BASE_ACTION: dict[str, StrategyAction] = {
    IntentLabel.PAYMENT: StrategyAction.ASK,
    IntentLabel.PROMISE_TO_PAY: StrategyAction.CONFIRM,
    IntentLabel.DISPUTE: StrategyAction.VERIFY,
    IntentLabel.HARDSHIP: StrategyAction.REASSURE,
    IntentLabel.CALLBACK: StrategyAction.CONFIRM,
    IntentLabel.UNAVAILABLE: StrategyAction.ASK,
    IntentLabel.DISCONNECT: StrategyAction.CLOSE,
    IntentLabel.ABUSE: StrategyAction.ESCALATE,
    IntentLabel.IDENTITY_VERIFY: StrategyAction.VERIFY,
    IntentLabel.CONSENT_GRANT: StrategyAction.ASK,
    IntentLabel.CONSENT_REVOKE: StrategyAction.CLOSE,
    IntentLabel.SILENCE: StrategyAction.ASK,
    IntentLabel.OTHER: StrategyAction.ASK,
}

_STATE_BASE_ACTION: dict[str, StrategyAction] = {
    # Keyed by the real ConversationState enum values (src/engines/conversation_state)
    # — was previously keyed by ad hoc strings ("VERIFICATION", "PROMISE_TO_PAY",
    # "DISPUTE_HANDLING", "HARDSHIP_HANDLING") emitted only by the now-removed
    # ResponsePlanningEngine._infer_state() heuristic, which never matched this
    # engine's real caller (ConversationStateIntelligence).
    "GREETING": StrategyAction.VERIFY,
    "IDENTITY_VERIFICATION": StrategyAction.VERIFY,
    "DEBT_DISCUSSION": StrategyAction.ASK,
    "NEGOTIATION": StrategyAction.NEGOTIATE,
    "COMMITMENT_CAPTURE": StrategyAction.CONFIRM,
    "OBJECTION_HANDLING": StrategyAction.VERIFY,
    "ESCALATION": StrategyAction.ESCALATE,
    "CLOSING": StrategyAction.CLOSE,
    "POST_CALL": StrategyAction.CLOSE,
}


class StrategySelection:
    """Output of StrategyEngine.select() — the chosen action with metadata."""

    __slots__ = ("action", "confidence", "rationale")

    def __init__(self, action: StrategyAction, confidence: float, rationale: str) -> None:
        self.action = action
        self.confidence = confidence
        self.rationale = rationale

    def __repr__(self) -> str:
        return f"StrategySelection(action={self.action!r}, confidence={self.confidence:.2f})"


class StrategyEngine:
    """Selects the next conversational action deterministically.

    Applies strict precedence: de-escalate > comply > verify > collect.
    All logic is lookup-table and scoring-based — zero LLM calls.

    Architecture: V2 Ch4.
    """

    def select(
        self,
        primary_intent: str,
        conversation_state: str,
        risk: RiskAssessment | None = None,
        stress_level: StressLevel | None = None,
        identity_verified: bool = False,
    ) -> StrategySelection:
        """Select the best action for the current turn.

        Args:
            primary_intent: Primary IntentLabel value (string) from IntentEngine.
            conversation_state: ConversationState value (string) from
                                ConversationStateIntelligence.
            risk: Risk assessment from RiskEngine.
            stress_level: Customer stress level from EmotionIntelligenceEngine.
            identity_verified: Whether identity has been confirmed this session.

        Returns:
            StrategySelection with the chosen action, confidence, and rationale.
        """
        risk_flags: list[RiskFlag] = risk.flags if risk else []
        result = self._resolve(primary_intent, conversation_state, risk_flags, stress_level, identity_verified)
        logger.debug(
            "Strategy selected",
            extra={
                "primary_intent": primary_intent,
                "conversation_state": conversation_state,
                "action": result.action.value,
                "confidence": result.confidence,
                "risk_flags": [f.value for f in risk_flags],
            },
        )
        return result

    def _resolve(
        self,
        primary_intent: str,
        conversation_state: str,
        risk_flags: list[RiskFlag],
        stress_level: StressLevel | None,
        identity_verified: bool,
    ) -> StrategySelection:
        """Apply decision precedence rules and return the chosen action."""

        # Precedence 1: Immediate safety / escalation
        if RiskFlag.ABUSE_DETECTED in risk_flags:
            return StrategySelection(StrategyAction.ESCALATE, 1.0, "ABUSE_DETECTED — mandatory escalation")

        if RiskFlag.LEGAL_THREAT in risk_flags:
            return StrategySelection(StrategyAction.ESCALATE, 0.95, "LEGAL_THREAT — escalating per policy")

        # Precedence 2: Compliance / consent
        if RiskFlag.CONSENT_RISK in risk_flags:
            return StrategySelection(StrategyAction.CLOSE, 0.9, "CONSENT_RISK — honour consent withdrawal")

        # Precedence 3: Verification gate
        if not identity_verified and conversation_state in ("GREETING", "IDENTITY_VERIFICATION"):
            return StrategySelection(
                StrategyAction.VERIFY, 0.95, "Identity not yet verified — must verify before disclosure"
            )

        if primary_intent == IntentLabel.DISPUTE or RiskFlag.DISPUTE_CLAIM in risk_flags:
            return StrategySelection(StrategyAction.VERIFY, 0.9, "DISPUTE detected — verify account details")

        # Precedence 4: De-escalation
        if (
            RiskFlag.HARDSHIP_INDICATOR in risk_flags
            or primary_intent == IntentLabel.HARDSHIP
            or stress_level in (StressLevel.HIGH, StressLevel.CRITICAL)
        ):
            return StrategySelection(StrategyAction.REASSURE, 0.85, "Hardship/high-stress — reassure customer")

        # Precedence 5: Intent-driven action
        intent_action = _INTENT_BASE_ACTION.get(primary_intent)
        if intent_action is not None:
            # State may upgrade intent action when state implies a stronger directive
            state_action = _STATE_BASE_ACTION.get(conversation_state)
            if state_action in (StrategyAction.NEGOTIATE, StrategyAction.CLOSE) and intent_action == StrategyAction.ASK:
                return StrategySelection(
                    state_action, 0.8, f"State={conversation_state} promotes ASK→{state_action.value}"
                )
            return StrategySelection(intent_action, 0.8, f"Intent={primary_intent}→{intent_action.value}")

        # Precedence 6: State-driven default
        state_action = _STATE_BASE_ACTION.get(conversation_state, StrategyAction.ASK)
        return StrategySelection(state_action, 0.6, f"Default for state={conversation_state}")
