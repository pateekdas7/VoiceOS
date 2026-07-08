"""AdaptiveConversationEngine — runtime adaptation based on loops and silence.

Combines ConversationLoopDetector and SilenceRecoveryPolicy to produce an
AdaptiveSignal that informs the ResponsePlanningEngine's strategy selection.

No LLM calls are made; all logic is deterministic.

Architecture: V2 Ch1 (Conversation Engine — adaptive behaviour).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.libs.contracts.response_plan import IntentLabel

from .loop_detector import ConversationLoopDetector
from .silence_handler import SilenceRecoveryPolicy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdaptiveSignal:
    """Output of AdaptiveConversationEngine for one turn.

    Carries loop-detection results and silence-recovery guidance.
    """

    loop_detected: bool
    """True when the recent intent history shows a repetitive cycle."""

    loop_count: int
    """Maximum repeat count of any single intent in the recent window."""

    silence_recovery_text: str | None
    """Recovery prompt to prepend when silence_ms exceeds the light threshold.
    None when no recovery is needed."""

    escalation_recommended: bool
    """True when prolonged silence or persistent loop warrants human handoff."""

    recommended_action: str
    """Coarse guidance: 'continue' | 'clarify' | 'escalate' | 'close'."""


_LOOP_ESCALATION_THRESHOLD = 4


class AdaptiveConversationEngine:
    """Generates adaptive signals from conversation history and silence.

    Args:
        loop_detector: Injected ConversationLoopDetector.
        silence_policy: Injected SilenceRecoveryPolicy.
    """

    def __init__(
        self,
        loop_detector: ConversationLoopDetector | None = None,
        silence_policy: SilenceRecoveryPolicy | None = None,
    ) -> None:
        self._loop = loop_detector or ConversationLoopDetector()
        self._silence = silence_policy or SilenceRecoveryPolicy()

    def process(
        self,
        intent_history: list[IntentLabel],
        silence_duration_ms: int = 0,
    ) -> AdaptiveSignal:
        """Compute the adaptive signal for this turn.

        Args:
            intent_history: Ordered list of recent intent labels (most-recent last).
            silence_duration_ms: Customer silence duration in milliseconds.

        Returns:
            AdaptiveSignal with loop detection and silence recovery results.
        """
        loop_detected, loop_count = self._loop.detect(intent_history)
        recovery_text = self._silence.get_recovery_text(silence_duration_ms)
        silence_escalation = self._silence.requires_escalation(silence_duration_ms)
        loop_escalation = loop_count >= _LOOP_ESCALATION_THRESHOLD

        escalation_recommended = silence_escalation or loop_escalation

        if escalation_recommended:
            action = "escalate"
        elif recovery_text is not None:
            action = "clarify"
        elif loop_detected:
            action = "clarify"
        else:
            action = "continue"

        signal = AdaptiveSignal(
            loop_detected=loop_detected,
            loop_count=loop_count,
            silence_recovery_text=recovery_text,
            escalation_recommended=escalation_recommended,
            recommended_action=action,
        )

        logger.debug(
            "AdaptiveSignal computed",
            extra={
                "loop_detected": loop_detected,
                "loop_count": loop_count,
                "silence_ms": silence_duration_ms,
                "action": action,
            },
        )
        return signal
