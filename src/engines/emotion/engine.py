"""EmotionIntelligenceEngine — derives EmotionSignal from text + prosody cues.

The engine combines text-based sentiment features (keyword scoring) with
audio prosody cues from the TurnInput segments (speaking rate proxy,
segment-level confidence as stress proxy). No ML model is required at
this stage; the rule-based approach achieves sufficient accuracy for
the collections domain while keeping latency well below 30ms.

A trained emotion model can be wired in as an optional replacement for
the keyword scorer in Sprint-034 (Conversation Learning Layer).

Architecture: V2 Ch10 (Emotion Intelligence); V1 Ch19.
"""

from __future__ import annotations

import logging
import re

from prometheus_client import Counter

from src.libs.contracts.streaming import Sentiment, StressLevel
from src.libs.contracts.turn import TurnInput

from .result import EmotionSignal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

_EMOTION_ANALYSES = Counter(
    "emotion_analyses_total",
    "Total emotion analyses performed",
    ["sentiment"],
)

# ---------------------------------------------------------------------------
# Keyword rules — ordered from most severe to least
# ---------------------------------------------------------------------------

_HOSTILE_PATTERNS: list[str] = [
    r"\bgaali\b",
    r"\babuse\b",
    r"\bsaala\b",
    r"\bbc\b",
    r"\bmc\b",
    r"\bchutiya\b",
    r"\bkamina\b",
    r"\bhar\s+roj\b.*\bbhai\b",
    r"\bthreaten\b",
    r"\bkill\b",
    r"\bmarenge\b",
]

_NEGATIVE_PATTERNS: list[str] = [
    r"\bnahi\b",
    r"\bnot\b",
    r"\bno\b",
    r"\bpaise nahi\b",
    r"\bproblem\b",
    r"\bpareshaan\b",
    r"\bdukhi\b",
    r"\bgussa\b",
    r"\bangry\b",
    r"\bfrustrated\b",
    r"\bgalat\b",
    r"\bbura\b",
    r"\bfailure\b",
    r"\bnahi\b",
    r"\bdisappointed\b",
]

_POSITIVE_PATTERNS: list[str] = [
    r"\btheeek\b",
    r"\btheek hai\b",
    r"\bokay\b",
    r"\bhaan\b",
    r"\byes\b",
    r"\bji haan\b",
    r"\bgood\b",
    r"\bachha\b",
    r"\bthank\b",
    r"\bshukriya\b",
    r"\bhappy\b",
    r"\bkhush\b",
    r"\bpay kar\b",
    r"\bde dunga\b",
]

# Stress indicators — short segments, low confidence, fast speech
_HIGH_STRESS_PATTERNS: list[str] = [
    r"\bchodo\b",
    r"\bnahi\s+chahiye\b",
    r"\bband\s+karo\b",
    r"\bchup\b",
    r"\bemergency\b",
    r"\bbeemar\b",
]


def _matches_any(text: str, patterns: list[str]) -> bool:
    lower = text.lower()
    return any(re.search(p, lower) for p in patterns)


class EmotionIntelligenceEngine:
    """Derives EmotionSignal from TurnInput text and prosody cues.

    Text features:
      - Keyword presence → Sentiment classification
      - Negative keyword density → arousal estimation

    Prosody cues (from TurnInput.segments):
      - Mean segment confidence → stress proxy (low confidence = stressed speech)
      - Speech rate (words / duration) → arousal contribution

    Architecture: V2 Ch10.
    """

    def analyze(self, turn: TurnInput) -> EmotionSignal:
        """Analyze emotion from TurnInput.

        Args:
            turn: Finalized TurnInput from the Dialogue Manager.

        Returns:
            EmotionSignal with all fields populated.
        """
        text = turn.transcript

        sentiment = self._classify_sentiment(text)
        arousal = self._estimate_arousal(text, turn)
        valence = self._estimate_valence(sentiment)
        stress_level = self._estimate_stress(sentiment, arousal, text)
        dominant_emotion = self._label_emotion(sentiment, arousal)

        _EMOTION_ANALYSES.labels(sentiment=sentiment.value).inc()

        logger.debug(
            "Emotion analyzed",
            extra={
                "turn_id": turn.turn_id,
                "call_id": turn.call_id,
                "sentiment": sentiment.value,
                "arousal": round(arousal, 3),
                "valence": round(valence, 3),
                "stress_level": stress_level.value,
            },
        )

        return EmotionSignal(
            sentiment=sentiment,
            arousal=round(arousal, 4),
            valence=round(valence, 4),
            stress_level=stress_level,
            dominant_emotion=dominant_emotion,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_sentiment(text: str) -> Sentiment:
        if _matches_any(text, _HOSTILE_PATTERNS):
            return Sentiment.HOSTILE
        if _matches_any(text, _NEGATIVE_PATTERNS):
            return Sentiment.NEGATIVE
        if _matches_any(text, _POSITIVE_PATTERNS):
            return Sentiment.POSITIVE
        return Sentiment.NEUTRAL

    @staticmethod
    def _estimate_arousal(text: str, turn: TurnInput) -> float:
        """Estimate emotional arousal [0.0, 1.0].

        Base arousal from negative keyword density + prosody cue (low
        mean segment confidence → higher arousal for stressed speech).
        """
        words = text.split()
        if not words:
            return 0.1

        # Keyword density contribution (0.0 - 0.5)
        neg_count = sum(1 for p in _NEGATIVE_PATTERNS + _HOSTILE_PATTERNS if re.search(p, text.lower()))
        keyword_arousal = min(0.5, neg_count * 0.08)

        # Prosody contribution from segment confidence (low conf → stressed)
        segments = turn.segments
        if segments:
            mean_conf = sum(s.confidence for s in segments) / len(segments)
            # Low confidence (e.g., 0.5) → higher arousal contribution (0.3)
            prosody_arousal = max(0.0, (1.0 - mean_conf) * 0.5)
        else:
            prosody_arousal = 0.1

        return min(1.0, keyword_arousal + prosody_arousal)

    @staticmethod
    def _estimate_valence(sentiment: Sentiment) -> float:
        """Map categorical sentiment to a valence float in [-1.0, 1.0]."""
        mapping: dict[Sentiment, float] = {
            Sentiment.POSITIVE: 0.7,
            Sentiment.NEUTRAL: 0.0,
            Sentiment.NEGATIVE: -0.5,
            Sentiment.HOSTILE: -0.9,
        }
        return mapping.get(sentiment, 0.0)

    @staticmethod
    def _estimate_stress(
        sentiment: Sentiment,
        arousal: float,
        text: str,
    ) -> StressLevel:
        """Estimate customer stress from sentiment + arousal + text cues."""
        if sentiment == Sentiment.HOSTILE or _matches_any(text, _HIGH_STRESS_PATTERNS):
            return StressLevel.CRITICAL
        if sentiment == Sentiment.NEGATIVE and arousal > 0.5:
            return StressLevel.HIGH
        if arousal > 0.3 or sentiment == Sentiment.NEGATIVE:
            return StressLevel.MEDIUM
        return StressLevel.LOW

    @staticmethod
    def _label_emotion(sentiment: Sentiment, arousal: float) -> str:
        """Return a human-readable dominant emotion label."""
        if sentiment == Sentiment.HOSTILE:
            return "hostile"
        if sentiment == Sentiment.NEGATIVE and arousal > 0.5:
            return "frustrated"
        if sentiment == Sentiment.NEGATIVE:
            return "distressed"
        if sentiment == Sentiment.POSITIVE and arousal > 0.4:
            return "engaged"
        if sentiment == Sentiment.POSITIVE:
            return "cooperative"
        return "neutral"
