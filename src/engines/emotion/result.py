"""EmotionSignal — the output type of EmotionIntelligenceEngine.

EmotionSignal is the engine-internal result type and differs from the
contracts-layer EmotionSpec (which uses float sentiment). The Conversation
Engine (Sprint-012) converts EmotionSignal → EmotionSpec when building
the ResponsePlan.

Architecture: V2 Ch10 (Emotion Intelligence); V1 Ch19.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from src.libs.contracts.streaming import Sentiment, StressLevel


class EmotionSignal(BaseModel):
    """Full emotion analysis result from EmotionIntelligenceEngine.

    Carries categorical sentiment (enum), arousal level, valence, and
    estimated customer stress level. Used by the EmpathyPlanner (Sprint-011)
    to select tone, pacing, and empathy parameters.
    """

    model_config = ConfigDict(frozen=True)

    sentiment: Sentiment
    """Broad sentiment category: POSITIVE | NEUTRAL | NEGATIVE | HOSTILE."""

    arousal: float = Field(ge=0.0, le=1.0)
    """Emotional arousal (activation) level [0.0, 1.0]. High = distressed/excited."""

    valence: float = Field(ge=-1.0, le=1.0)
    """Emotional valence: -1.0 = very negative, 0.0 = neutral, 1.0 = very positive."""

    stress_level: StressLevel
    """Estimated customer stress level: LOW | MEDIUM | HIGH | CRITICAL."""

    dominant_emotion: str = "neutral"
    """Human-readable dominant emotion label for audit/explainability."""
