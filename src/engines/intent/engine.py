"""IntentEngine — classifies TurnInput into one of 13 intent labels.

The IntentEngine is the first stage of the Perception Layer. It receives
a finalized TurnInput from the Dialogue Manager and produces an IntentResult
carrying the classified label, confidence score, raw logits, and a
reasoning hint.

The engine is CPU-resident and targets p99 < 30ms inference latency using
the ONNX runtime. Business logic for what to DO with the intent lives
upstream (Sprint-011 decision engines) — this engine only classifies.

Architecture: V2 Ch3 (Intent Engine — 13 labels, <30ms).
Invariants: RI-5 (Law of Authority — classification is evidence, not fact).
"""

from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING

from prometheus_client import Counter, Histogram

from src.libs.contracts.response_plan import IntentLabel
from src.libs.contracts.turn import TurnInput

from .model import IntentModel
from .result import IntentResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

_INTENT_CLASSIFICATIONS = Counter(
    "intent_classifications_total",
    "Total number of intent classifications performed",
    ["label"],
)

_INTENT_LATENCY = Histogram(
    "intent_classification_latency_seconds",
    "IntentEngine inference latency in seconds",
    buckets=[0.005, 0.010, 0.015, 0.020, 0.025, 0.030, 0.050, 0.100],
)

# ---------------------------------------------------------------------------
# Label order: must match IntentLabel declaration order for score indexing
# ---------------------------------------------------------------------------

_LABEL_ORDER: list[IntentLabel] = list(IntentLabel)


def _softmax(logits: list[float]) -> list[float]:
    """Numerically stable softmax over a list of logits."""
    max_val = max(logits)
    exps = [math.exp(x - max_val) for x in logits]
    total = sum(exps)
    return [e / total for e in exps]


class IntentEngine:
    """Classifies a TurnInput into one of 13 intent labels.

    Architecture: V2 Ch3.
    Target: p99 < 30ms on CPU (ONNX inference).

    Usage:
        model = IntentModel.from_mock()          # tests
        model = IntentModel.from_onnx(path)      # production
        engine = IntentEngine(model)
        result = engine.classify(turn_input)
    """

    def __init__(self, model: IntentModel) -> None:
        self._model = model

    def classify(self, turn: TurnInput) -> IntentResult:
        """Classify the customer utterance in TurnInput.

        Args:
            turn: Finalized TurnInput from the Dialogue Manager.

        Returns:
            IntentResult with the top label, confidence, raw scores,
            and a reasoning hint.

        Note:
            Unknown or ambiguous input returns IntentLabel.OTHER rather than
            raising an exception (Law of Authority: classification is evidence).
        """
        t0 = time.perf_counter()

        transcript = turn.transcript.strip()
        raw_logits = self._model.infer(transcript)

        if len(raw_logits) != IntentModel.NUM_LABELS:
            logger.warning(
                "IntentModel returned %d scores; expected %d — defaulting to OTHER",
                len(raw_logits),
                IntentModel.NUM_LABELS,
            )
            probabilities = [0.0] * IntentModel.NUM_LABELS
            other_idx = _LABEL_ORDER.index(IntentLabel.OTHER)
            probabilities[other_idx] = 1.0
        else:
            probabilities = _softmax(raw_logits)

        top_idx = probabilities.index(max(probabilities))
        top_label = _LABEL_ORDER[top_idx]
        confidence = probabilities[top_idx]

        # Build reasoning hint from first matched keyword (if keyword mode)
        reasoning_hint = self._build_hint(transcript, top_label)

        elapsed = time.perf_counter() - t0
        _INTENT_LATENCY.observe(elapsed)
        _INTENT_CLASSIFICATIONS.labels(label=top_label.value).inc()

        logger.debug(
            "Intent classified",
            extra={
                "turn_id": turn.turn_id,
                "call_id": turn.call_id,
                "label": top_label.value,
                "confidence": round(confidence, 4),
                "latency_ms": round(elapsed * 1000, 2),
            },
        )

        return IntentResult(
            label=top_label,
            confidence=round(confidence, 6),
            raw_scores=tuple(probabilities),
            reasoning_hint=reasoning_hint,
            source_span=transcript[:80] if len(transcript) > 80 else transcript,
        )

    @staticmethod
    def _build_hint(transcript: str, label: IntentLabel) -> str:
        """Return a short reasoning hint string for explainability."""
        if not transcript:
            return "empty transcript"
        return f"classified as {label.value} from: {transcript[:40]!r}"
