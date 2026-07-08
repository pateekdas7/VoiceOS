"""IntentResult — the richer internal output of IntentEngine.

IntentResult carries raw classifier logits, probabilities, and a
reasoning hint alongside the public-API IntentSignal fields. The
engine returns IntentResult; callers that need the contracts-layer
IntentSignal call IntentResult.to_signal().

Architecture: V2 Ch3.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from src.libs.contracts.response_plan import IntentLabel, IntentSignal


class IntentResult(BaseModel):
    """Full intent classification result from IntentEngine.

    The contracts-layer IntentSignal is a subset of this type.
    Use to_signal() to obtain the public API form.
    """

    model_config = ConfigDict(frozen=True)

    label: IntentLabel
    """Top-ranked intent label."""

    confidence: float = Field(ge=0.0, le=1.0)
    """Softmax probability for the top label [0.0, 1.0]."""

    raw_scores: tuple[float, ...] = ()
    """Raw logit scores for all 13 labels in IntentLabel declaration order."""

    reasoning_hint: str = ""
    """Keyword or span that drove the classification (for explainability)."""

    source_span: str = ""
    """Utterance span matched during classification."""

    def to_signal(self) -> IntentSignal:
        """Convert to the contracts-layer IntentSignal (public API type)."""
        return IntentSignal(
            label=self.label,
            confidence=self.confidence,
            source_span=self.source_span,
        )
