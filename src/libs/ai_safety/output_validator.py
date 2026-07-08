"""AIOutputValidator — format/length checks on LLM output (V4 Ch14).

Distinct from ``src.services.llm_runtime.output_validator.OutputValidator``
(Sprint-009/012, which checks amount/fact grounding against the sealed
ResponsePlan) — this is the AI-safety-layer's cheap structural check:
length bounds and control-character hygiene, run before the content and
grounding checks (V4 Ch14 §14.9 pipeline order).

Architecture: V4 Ch14 (AI Safety) §14.7, §14.12.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MAX_LENGTH = 800
DEFAULT_MIN_LENGTH = 1


@dataclass(frozen=True)
class OutputValidationResult:
    """Outcome of a format/length check on candidate LLM output."""

    valid: bool
    reason: str | None = None


class AIOutputValidator:
    """Cheap structural checks on LLM output: length bounds + control characters."""

    def __init__(self, max_length: int = DEFAULT_MAX_LENGTH, min_length: int = DEFAULT_MIN_LENGTH) -> None:
        self._max_length = max_length
        self._min_length = min_length

    def validate(self, text: str) -> OutputValidationResult:
        if len(text) < self._min_length:
            return OutputValidationResult(valid=False, reason="output is empty")

        if len(text) > self._max_length:
            return OutputValidationResult(valid=False, reason=f"output exceeds {self._max_length} characters")

        if any(ord(char) < 0x20 and char not in "\n\t" for char in text):
            return OutputValidationResult(valid=False, reason="output contains control characters")

        return OutputValidationResult(valid=True)
