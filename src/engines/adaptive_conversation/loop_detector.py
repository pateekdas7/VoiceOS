"""ConversationLoopDetector — detects repeated intent cycles.

A loop is defined as the same IntentLabel appearing 3 or more times in
the recent intent history. Once a loop is detected the agent should
change strategy (escalate, close, or clarify) to break the cycle.

Architecture: V2 Ch1 (Conversation Engine — adaptive behaviour).
"""

from __future__ import annotations

from collections import Counter

from src.libs.contracts.response_plan import IntentLabel

_DEFAULT_WINDOW = 5
_DEFAULT_THRESHOLD = 3


class ConversationLoopDetector:
    """Detects when the conversation is cycling on the same intent.

    Examines a sliding window of recent intent labels. If any single
    label appears at least ``threshold`` times within the window, a
    loop is declared.

    Args:
        window: Number of recent turns to examine.
        threshold: Minimum repetitions required to declare a loop.
    """

    def __init__(self, window: int = _DEFAULT_WINDOW, threshold: int = _DEFAULT_THRESHOLD) -> None:
        if window < 1:
            raise ValueError(f"window must be >= 1, got {window}")
        if threshold < 2:
            raise ValueError(f"threshold must be >= 2, got {threshold}")
        self._window = window
        self._threshold = threshold

    def detect(self, intent_history: list[IntentLabel]) -> tuple[bool, int]:
        """Check for a conversation loop in the recent intent history.

        Args:
            intent_history: Ordered list of intent labels, most-recent last.

        Returns:
            Tuple of (loop_detected: bool, max_repeat_count: int).
            max_repeat_count is the highest repetition count seen in the
            window even when no loop is declared.
        """
        if not intent_history:
            return False, 0

        recent = intent_history[-self._window :]
        counts = Counter(recent)
        max_count = max(counts.values())
        loop_detected = max_count >= self._threshold
        return loop_detected, max_count
