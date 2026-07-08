"""IntentModel — ONNX inference wrapper for intent classification.

The IntentModel wraps an ONNX-runtime session and exposes a single
``infer(text)`` call that returns a list of raw logit scores for the
13 intent labels. The model is CPU-resident with a target of p99 < 30ms.

Three operating modes:
  - ONNX mode:     load a real ONNX model from disk (production)
  - Keyword mode:  rule-based classification, no file required (AI eval)
  - Mock mode:     returns injected fixed scores (unit tests)

Architecture: V2 Ch3 (Intent Engine — ONNX inference, CPU, p99 < 30ms).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar

from src.libs.contracts.response_plan import IntentLabel

# ---------------------------------------------------------------------------
# Keyword rules for each intent label
# Used in keyword mode when no ONNX model is available.
# ---------------------------------------------------------------------------

_KEYWORD_RULES: list[tuple[IntentLabel, list[str]]] = [
    (IntentLabel.SILENCE, [r"^\s*$", r"^\.{1,3}$", r"^\[silence\]$"]),
    (IntentLabel.ABUSE, [r"\bgarl?i\b", r"\bbc\b", r"\bmc\b", r"\bgaali\b", r"\babuse\b", r"\bsaala\b"]),
    (IntentLabel.DISCONNECT, [r"\bkato\b", r"\bband karo\b", r"\bdisconnect\b", r"\bhang up\b", r"\bcut\b"]),
    (
        IntentLabel.CONSENT_REVOKE,
        [r"\bmat karo\b", r"\bconsent revoke\b", r"\bdnd\b", r"\bdo not call\b", r"\bnahi chahiye\b"],
    ),
    (
        IntentLabel.PROMISE_TO_PAY,
        [r"\bdunga\b", r"\bdungi\b", r"\bde dunga\b", r"\bpromise\b", r"\bwill pay\b", r"\bkal de\b", r"\bde denge\b"],
    ),
    (IntentLabel.CONSENT_GRANT, [r"\bconsent\b", r"\bhaan\b", r"\byes\b", r"\bagree\b", r"\bji haan\b", r"\bokay\b"]),
    (
        IntentLabel.IDENTITY_VERIFY,
        [r"\bnaam\b", r"\bname\b", r"\bdate of birth\b", r"\bdob\b", r"\bpehchaan\b", r"\bverify\b"],
    ),
    (
        IntentLabel.PAYMENT,
        [r"\bpay\b", r"\bbhugtan\b", r"\bde raha\b", r"\bde deta\b", r"\bpayment\b", r"\bpaise de\b"],
    ),
    (
        IntentLabel.DISPUTE,
        [r"\bgalat\b", r"\bdispute\b", r"\bwrong\b", r"\bincorrect\b", r"\bvivad\b", r"\bnahi bana\b"],
    ),
    (
        IntentLabel.HARDSHIP,
        [
            r"\bpaise nahi\b",
            r"\bnahi hai paise\b",
            r"\bhardship\b",
            r"\bmushkil\b",
            r"\bberozgar\b",
            r"\bjob nahi\b",
            r"\bno money\b",
        ],
    ),
    (
        IntentLabel.CALLBACK,
        [r"\bbaad mein\b", r"\blater\b", r"\bcallback\b", r"\bdoosri baar\b", r"\bshaam ko\b", r"\bkal call\b"],
    ),
    (
        IntentLabel.UNAVAILABLE,
        [r"\bupalabdh nahi\b", r"\bnot available\b", r"\bbusy\b", r"\bnahi hun\b", r"\babhi nahi\b"],
    ),
]


class IntentModel:
    """CPU-resident intent classifier.

    Operating modes (evaluated in priority order):
      1. Mock mode:    mock_scores is not None — return fixed scores
      2. ONNX mode:    model_path is not None — load and run ONNX session
      3. Keyword mode: fallback rule-based classifier (no file required)
    """

    NUM_LABELS: ClassVar[int] = 13

    def __init__(
        self,
        model_path: Path | None = None,
        *,
        mock_scores: list[float] | None = None,
    ) -> None:
        self._mock_scores: list[float] | None = mock_scores
        self._session: Any = None

        if mock_scores is None and model_path is not None:
            import onnxruntime as ort

            self._session = ort.InferenceSession(
                str(model_path),
                providers=["CPUExecutionProvider"],
            )

    @classmethod
    def from_onnx(cls, model_path: Path) -> IntentModel:
        """Load a real ONNX model from disk."""
        return cls(model_path=model_path)

    @classmethod
    def from_mock(cls, scores: list[float] | None = None) -> IntentModel:
        """Create a mock model for unit tests.

        Args:
            scores: Fixed logit scores to return for every call (length 13).
                    If None, returns a uniform distribution.
        """
        default: list[float] = [1.0 / cls.NUM_LABELS] * cls.NUM_LABELS
        return cls(mock_scores=scores if scores is not None else default)

    def infer(self, text: str) -> list[float]:
        """Return raw logit scores for all 13 intent labels.

        Args:
            text: The utterance transcript to classify.

        Returns:
            List of 13 float logit scores (not yet softmaxed).
        """
        if self._mock_scores is not None:
            return list(self._mock_scores)
        if self._session is not None:
            return self._run_onnx(text)
        return self._keyword_classify(text)

    # ------------------------------------------------------------------
    # ONNX inference
    # ------------------------------------------------------------------

    def _run_onnx(self, text: str) -> list[float]:
        """Run inference on an ONNX session.

        The ONNX model expects a single string input named 'text'.
        This matches the exported production model format (V2 Ch3).
        """
        import numpy as np

        input_name: str = self._session.get_inputs()[0].name
        output_name: str = self._session.get_outputs()[0].name
        inputs: dict[str, Any] = {input_name: np.array([[text]])}
        raw: list[Any] = self._session.run([output_name], inputs)
        scores: list[float] = raw[0][0].tolist()
        return scores

    # ------------------------------------------------------------------
    # Keyword-based fallback classifier
    # ------------------------------------------------------------------

    def _keyword_classify(self, text: str) -> list[float]:
        """Rule-based keyword scorer for 13 intent labels.

        Iterates rules in priority order. First match dominates.
        Returns scores: matched label gets 10.0, others get 0.0.
        """
        lower = text.lower().strip()
        label_order = [lbl for lbl, _ in _KEYWORD_RULES] + [IntentLabel.OTHER]
        scores: dict[IntentLabel, float] = dict.fromkeys(IntentLabel, 0.0)

        for label, patterns in _KEYWORD_RULES:
            for pattern in patterns:
                if re.search(pattern, lower):
                    scores[label] = 10.0
                    # Return immediately on first match
                    return [scores[lbl] for lbl in IntentLabel]

        # No match → OTHER
        scores[IntentLabel.OTHER] = 10.0
        _ = label_order  # used for documentation only
        return [scores[lbl] for lbl in IntentLabel]
