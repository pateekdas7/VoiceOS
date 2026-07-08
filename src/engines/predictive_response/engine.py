"""PredictiveResponseEngine — pre-computes response plans from partial STT.

Listens to partial WordHypothesis tokens as the customer speaks and attempts
to predict the most likely intent early. When a high-confidence intent is
predicted, a ResponsePlan is cached. On turn completion, if the finalised
intent matches the predicted intent, the cached plan is returned immediately,
reducing first-audio latency.

On barge-in the cache is invalidated.

This is a Sprint-012 walking skeleton implementation — intent prediction uses
a simple keyword scan rather than the full IntentEngine (ONNX). The interface
is identical to the production design so tests remain valid.

Architecture: V1 Ch18 (True Streaming Pipeline); V2 Ch1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.libs.contracts.response_plan import IntentLabel, ResponsePlan
from src.libs.contracts.streaming import WordHypothesis

logger = logging.getLogger(__name__)

# Keywords used for early-intent detection from partial transcripts.
_INTENT_KEYWORDS: dict[IntentLabel, list[str]] = {
    IntentLabel.PAYMENT: ["pay", "payment", "bhugtaan", "bhugtan", "dena"],
    IntentLabel.DISPUTE: ["dispute", "galat", "wrong", "incorrect", "nahin"],
    IntentLabel.HARDSHIP: ["hardship", "pareshaan", "problem", "takleef", "mushkil"],
    IntentLabel.CALLBACK: ["callback", "baad mein", "later", "call back"],
    IntentLabel.PROMISE_TO_PAY: ["promise", "vaada", "zaroor", "definitely"],
}

_CONFIDENCE_THRESHOLD = 0.8


@dataclass
class _PredictiveState:
    """Mutable state for one active prediction session."""

    partial_text: str = ""
    predicted_intent: IntentLabel | None = None
    confidence: float = 0.0
    cached_plan: ResponsePlan | None = None
    invalidated: bool = False
    partial_count: int = field(default=0, compare=False)


class PredictiveResponseEngine:
    """Caches ResponsePlans predicted from partial STT transcripts.

    One instance is created per call session. ``update_partial`` must be
    called for every partial WordHypothesis; ``get_cached_plan`` is called
    once the turn is finalised to retrieve a pre-computed plan if available.

    Architecture: V1 Ch18; V2 Ch1.
    """

    def __init__(self) -> None:
        self._state = _PredictiveState()

    def update_partial(self, hypothesis: WordHypothesis) -> None:
        """Feed a partial STT hypothesis to the predictor.

        Args:
            hypothesis: A partial (is_final=False) or final WordHypothesis.
        """
        if self._state.invalidated:
            return

        self._state.partial_text += f" {hypothesis.word}"
        self._state.partial_count += 1

        # Try to predict intent from accumulated partial text.
        predicted, confidence = self._predict_intent(self._state.partial_text.strip())
        if predicted is not None and confidence >= _CONFIDENCE_THRESHOLD:
            if predicted != self._state.predicted_intent:
                logger.debug(
                    "PredictiveResponseEngine: new early intent",
                    extra={"intent": predicted.value, "confidence": confidence},
                )
            self._state.predicted_intent = predicted
            self._state.confidence = confidence

    def _predict_intent(self, text: str) -> tuple[IntentLabel | None, float]:
        """Simple keyword-based intent prediction from partial text."""
        lower = text.lower()
        for intent, keywords in _INTENT_KEYWORDS.items():
            for kw in keywords:
                if kw in lower:
                    # Confidence grows with more partial words confirming the keyword.
                    base = 0.80 + min(0.15, self._state.partial_count * 0.02)
                    return intent, round(base, 4)
        return None, 0.0

    def prime_cache(self, intent: IntentLabel, plan: ResponsePlan) -> None:
        """Store a pre-computed plan for the predicted intent.

        Called by the ConversationEngine once a plan has been assembled
        speculatively while still waiting for end-of-utterance.

        Args:
            intent: The intent this plan was built for.
            plan: The pre-computed ResponsePlan.
        """
        if not self._state.invalidated and self._state.predicted_intent == intent:
            self._state.cached_plan = plan
            logger.debug("PredictiveResponseEngine: plan cached for %s", intent.value)

    def get_cached_plan(self, confirmed_intent: IntentLabel) -> ResponsePlan | None:
        """Return the cached plan if it matches the confirmed intent.

        Args:
            confirmed_intent: The intent confirmed from the final STT result.

        Returns:
            A cached ResponsePlan if available and matching, else None.
        """
        if self._state.invalidated:
            return None
        if self._state.cached_plan is not None and self._state.predicted_intent == confirmed_intent:
            logger.debug("PredictiveResponseEngine: cache HIT for %s", confirmed_intent.value)
            return self._state.cached_plan
        return None

    def invalidate(self) -> None:
        """Invalidate the cache — called on barge-in or turn reset."""
        self._state.invalidated = True
        self._state.cached_plan = None
        self._state.predicted_intent = None
        logger.debug("PredictiveResponseEngine: cache invalidated (barge-in)")

    def reset(self) -> None:
        """Reset state for a new turn."""
        self._state = _PredictiveState()
