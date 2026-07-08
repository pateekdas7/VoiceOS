"""EmpathyPlanner — adapts tone, pacing, and language based on emotion state.

Produces an EmpathyConfig that the AdaptiveProsodyEngine (Sprint-009) and
the LLM prompt builder consume to adjust delivery. All rules are deterministic;
no LLM is involved.

Core rules (from V2 Ch14):
  - StressLevel.CRITICAL → EMPATHETIC + SLOW + acknowledgment phrase required
  - StressLevel.HIGH → EMPATHETIC + SLOW + acknowledgment phrase required
  - StressLevel.MEDIUM → REASSURING + NORMAL
  - StressLevel.LOW + Sentiment.POSITIVE → FRIENDLY + NORMAL
  - StressLevel.LOW + Sentiment.NEUTRAL → NEUTRAL + NORMAL
  - Sentiment.HOSTILE → FIRM + SLOW (after ABUSE_DETECTED handled by RiskEngine)

Architecture: V2 Ch14 (Empathy Planner); V1 Ch19-20.
"""

from __future__ import annotations

import logging

from src.libs.contracts.streaming import (
    EmpathyConfig,
    LanguageRegister,
    Pacing,
    Sentiment,
    StressLevel,
    Tone,
)

logger = logging.getLogger(__name__)

# Acknowledgment phrases keyed by stress level (Hindi/Hinglish)
_ACKNOWLEDGMENT_PHRASES: dict[StressLevel, str] = {
    StressLevel.CRITICAL: "Main samajh sakta hoon, yeh bahut mushkil waqt hai.",
    StressLevel.HIGH: "Main samajh sakta hoon aapki takleef.",
    StressLevel.MEDIUM: "Haan ji, main sun raha hoon.",
}


class EmpathyPlanner:
    """Adapts agent tone, pacing, and register based on customer emotion.

    Produces EmpathyConfig deterministically from StressLevel and Sentiment.
    EmpathyConfig is then consumed by AdaptiveProsodyEngine for TTS delivery.

    Architecture: V2 Ch14.
    """

    def plan(
        self,
        stress_level: StressLevel,
        sentiment: Sentiment,
        preferred_language: str = "hi-IN",
    ) -> EmpathyConfig:
        """Determine tone, pacing, and language register for this turn.

        Args:
            stress_level: Customer stress level from EmotionIntelligenceEngine.
            sentiment: Customer sentiment from EmotionIntelligenceEngine.
            preferred_language: BCP-47 language preference from CustomerContext
                                (default: 'hi-IN').

        Returns:
            EmpathyConfig with tone, pacing, language_register, and optional
            acknowledgment phrase.
        """
        tone, pacing = self._select_tone_pacing(stress_level, sentiment)
        language_register = self._select_register(preferred_language)
        acknowledgment = self._select_acknowledgment(stress_level)

        config = EmpathyConfig(
            tone=tone,
            pacing=pacing,
            language_register=language_register,
            acknowledgment_phrase=acknowledgment,
        )

        logger.debug(
            "Empathy planned",
            extra={
                "stress_level": stress_level.value,
                "sentiment": sentiment.value,
                "tone": tone.value,
                "pacing": pacing.value,
                "language_register": language_register.value,
                "acknowledgment": acknowledgment is not None,
            },
        )
        return config

    @staticmethod
    def _select_tone_pacing(stress_level: StressLevel, sentiment: Sentiment) -> tuple[Tone, Pacing]:
        """Map stress/sentiment to (Tone, Pacing)."""
        if stress_level == StressLevel.CRITICAL:
            return Tone.EMPATHETIC, Pacing.SLOW

        if stress_level == StressLevel.HIGH:
            return Tone.EMPATHETIC, Pacing.SLOW

        if stress_level == StressLevel.MEDIUM:
            return Tone.REASSURING, Pacing.NORMAL

        # LOW stress — sentiment-driven
        if sentiment == Sentiment.HOSTILE:
            # Hostile but low stress (edge case) — firm, slow to de-escalate
            return Tone.FIRM, Pacing.SLOW

        if sentiment == Sentiment.NEGATIVE:
            return Tone.REASSURING, Pacing.NORMAL

        if sentiment == Sentiment.POSITIVE:
            return Tone.EMPATHETIC, Pacing.NORMAL

        # NEUTRAL
        return Tone.NEUTRAL, Pacing.NORMAL

    @staticmethod
    def _select_register(preferred_language: str) -> LanguageRegister:
        """Select language register from preferred language tag."""
        if preferred_language.startswith("hi"):
            return LanguageRegister.COLLOQUIAL  # Hindi — colloquial register
        if preferred_language.startswith("en"):
            return LanguageRegister.FORMAL  # English — formal register
        return LanguageRegister.SEMI_FORMAL  # Hinglish / mixed — semi-formal

    @staticmethod
    def _select_acknowledgment(stress_level: StressLevel) -> str | None:
        """Return an acknowledgment phrase for HIGH/CRITICAL stress, else None."""
        return _ACKNOWLEDGMENT_PHRASES.get(stress_level)
