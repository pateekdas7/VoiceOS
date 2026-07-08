"""PromptInjectionDetector — detects manipulation attempts in customer utterances (V4 Ch13/Ch14).

The primary defense against prompt injection is structural (the Law of
Authority — a successful injection still cannot alter facts/money/policy,
V4 Ch13 §13.12), so this detector is a *detective* control: flag known
manipulation patterns for logging/alerting and safe-fallback routing, not
the last line of defense.

Architecture: V4 Ch13 (Runtime Security) §13.7 (``screen_input``), §13.12.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern

_INJECTION_PATTERNS: tuple[Pattern[str], ...] = (
    re.compile(r"ignore (all |any |previous |above |prior )?instructions", re.IGNORECASE),
    re.compile(r"disregard (your|the) (rules|guidelines|instructions)", re.IGNORECASE),
    re.compile(r"system prompt", re.IGNORECASE),
    re.compile(r"reveal (your|the) (prompt|instructions|rules)", re.IGNORECASE),
    re.compile(r"you are now", re.IGNORECASE),
    re.compile(r"act as (if|though)", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
    re.compile(r"pretend (you are|to be)", re.IGNORECASE),
)


@dataclass(frozen=True)
class InjectionVerdict:
    """Outcome of screening one utterance for injection patterns (V4 Ch13 §13.6)."""

    flagged: bool
    matched_pattern: str | None = None


class PromptInjectionDetector:
    """Scans customer utterances for known prompt-injection/jailbreak patterns."""

    def __init__(self, patterns: tuple[Pattern[str], ...] = _INJECTION_PATTERNS) -> None:
        self._patterns = patterns

    def detect(self, text: str) -> InjectionVerdict:
        """Return the :class:`InjectionVerdict` for ``text``."""
        for pattern in self._patterns:
            if pattern.search(text):
                return InjectionVerdict(flagged=True, matched_pattern=pattern.pattern)
        return InjectionVerdict(flagged=False)
