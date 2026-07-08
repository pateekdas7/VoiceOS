"""SilenceRecoveryPolicy — generates clarification prompts for silent customers.

Tiered policy based on silence duration:
  <  800 ms  → no action (within normal pause tolerance)
  <  3 000 ms → light clarification ("Kya aap sun rahe hain?")
  < 10 000 ms → stronger clarification with topic re-statement
  ≥ 10 000 ms → escalation recommendation (human handoff or call close)

Architecture: V2 Ch1 (Dialogue Manager — silence handling).
"""

from __future__ import annotations

_TIER_NONE_MS = 800
_TIER_LIGHT_MS = 3_000
_TIER_STRONG_MS = 10_000

_MSG_LIGHT = "Kya aap sun rahe hain? Main aapki madad karne ke liye yahan hoon."
_MSG_STRONG = "Lagta hai aap busy hain. Kya aap abhi baat karna chahte hain?"
_MSG_ESCALATE = "Hum ek senior representative se aapki madad karwa sakte hain."


class SilenceRecoveryPolicy:
    """Produces recovery text based on customer silence duration.

    Args:
        light_threshold_ms: Silence below which only light recovery is used.
        strong_threshold_ms: Silence below which only strong recovery is used.
                             At or above this threshold, escalation is triggered.
    """

    def __init__(
        self,
        light_threshold_ms: int = _TIER_LIGHT_MS,
        strong_threshold_ms: int = _TIER_STRONG_MS,
    ) -> None:
        self._light_ms = light_threshold_ms
        self._strong_ms = strong_threshold_ms

    def get_recovery_text(self, silence_ms: int) -> str | None:
        """Return recovery text for the given silence duration, or None if silent pause is normal.

        Args:
            silence_ms: Duration of customer silence in milliseconds.

        Returns:
            Recovery text string, or None if no action needed.
        """
        if silence_ms < _TIER_NONE_MS:
            return None
        if silence_ms < self._light_ms:
            return _MSG_LIGHT
        if silence_ms < self._strong_ms:
            return _MSG_STRONG
        return _MSG_ESCALATE

    def requires_escalation(self, silence_ms: int) -> bool:
        """Return True if silence duration warrants human escalation."""
        return silence_ms >= self._strong_ms
