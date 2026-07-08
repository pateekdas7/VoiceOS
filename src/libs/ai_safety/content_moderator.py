"""ContentModerator — blocks abusive/threatening/biased LLM output (V4 Ch14).

Extracted as its own reusable component per Sprint-020.md; wired into
``AIGovernanceService``/``GovernanceLayer`` (Sprint-018) as a dedicated
safety-dimension check, in place of that layer's inline keyword check.

Architecture: V4 Ch14 (AI Safety) §14.7 (Public Interfaces), §14.12
(unsafe-response detection, toxicity detection).
"""

from __future__ import annotations

from dataclasses import dataclass

_ABUSIVE_KEYWORDS = ("madarchod", "behenchod", "chutiya", "saale", "bhosdike", "gandu")
"""Abuse/profanity — baseline keyword list (V4 Ch14 §14.12 toxicity detection)."""

_THREAT_KEYWORDS = (
    "main tumhe dekh lunga",
    "i will hurt you",
    "i'll hurt you",
    "you will regret",
    "i know where you live",
)
"""Coercion/threat phrasing — RBI prohibits coercion/threats (V4 Ch14 §14.12)."""

_BIAS_KEYWORDS = ("all women are", "all men are", "people like you always", "your kind")
"""Crude biased-generalization phrasing (V4 Ch14 §14.12 bias monitoring)."""


@dataclass(frozen=True)
class ModerationResult:
    """Outcome of a single content-moderation check (V4 Ch14 §14.6)."""

    safe: bool
    blocked_category: str | None = None


class ContentModerator:
    """Rule-based content moderator for LLM candidate output."""

    def __init__(
        self,
        abusive_keywords: tuple[str, ...] = _ABUSIVE_KEYWORDS,
        threat_keywords: tuple[str, ...] = _THREAT_KEYWORDS,
        bias_keywords: tuple[str, ...] = _BIAS_KEYWORDS,
    ) -> None:
        self._abusive_keywords = abusive_keywords
        self._threat_keywords = threat_keywords
        self._bias_keywords = bias_keywords

    def check(self, text: str) -> ModerationResult:
        """Check ``text`` for abuse, threats, or biased phrasing."""
        lowered = text.lower()

        if any(keyword in lowered for keyword in self._abusive_keywords):
            return ModerationResult(safe=False, blocked_category="ABUSE")

        if any(keyword in lowered for keyword in self._threat_keywords):
            return ModerationResult(safe=False, blocked_category="THREAT")

        if any(keyword in lowered for keyword in self._bias_keywords):
            return ModerationResult(safe=False, blocked_category="BIAS")

        return ModerationResult(safe=True)
