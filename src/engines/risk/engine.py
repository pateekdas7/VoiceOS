"""RiskEngine — deterministic risk signal detection.

Evaluates TurnInput + EmotionSignal to produce a RiskAssessment. All rules
are deterministic keyword/pattern rules — no LLM calls anywhere in this
module.

The engine applies rules in strict priority order:
  1. ABUSE_DETECTED — always triggers human_handoff_required (non-overridable)
  2. LEGAL_THREAT / DISPUTE_CLAIM — trigger escalation_required
  3. All other flags — informational; used by StrategyEngine for action scoring

Architecture: V2 Ch6 (Risk Engine).
"""

from __future__ import annotations

import logging
import re

from src.libs.contracts.streaming import Sentiment, StressLevel
from src.libs.contracts.turn import TurnInput

from .flags import RiskFlag
from .result import RiskAssessment

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword rule definitions — ordered from most severe to least
# ---------------------------------------------------------------------------

_ABUSE_PATTERNS: list[str] = [
    r"\bgaali\b",
    r"\babuse\b",
    r"\bharamkhor\b",
    r"\bkamina\b",
    r"\bchutiya\b",
    r"\bsaala\b",
    r"\bbc\b",
    r"\bmc\b",
    r"\bmarenge\b",
    r"\bkill\b",
    r"\bthreaten\b",
    r"\bjaan\s+se\s+maar\b",
    r"\bghar\s+aao\b",
]

_LEGAL_THREAT_PATTERNS: list[str] = [
    r"\bcourt\b",
    r"\blawyer\b",
    r"\bnalsa\b",
    r"\bconsumer\s+forum\b",
    r"\bfir\b",
    r"\bpolice\b",
    r"\bsue\b",
    r"\blegal\s+action\b",
    r"\bvakeel\b",
    r"\bthana\b",
    r"\bkehdena\s+court\b",
]

_DISPUTE_PATTERNS: list[str] = [
    r"\bwrong\s+amount\b",
    r"\bnot\s+my\s+loan\b",
    r"\bnahi\s+liya\b",
    r"\bgalat\s+hai\b",
    r"\bdispute\b",
    r"\bfraud\b",
    r"\bgalat\s+account\b",
    r"\byeh\s+mera\s+nahi\b",
    r"\bwrong\s+number\b",
]

_HARDSHIP_PATTERNS: list[str] = [
    r"\bjob\s+gaya\b",
    r"\bnaukri\s+chali\s+gayi\b",
    r"\bbeemar\b",
    r"\baccident\b",
    r"\bhospital\b",
    r"\bhardship\b",
    r"\bkoi\s+kaam\s+nahi\b",
    r"\bunemployed\b",
    r"\bno\s+income\b",
    r"\bincome\s+nahi\b",
    r"\bpaise\s+nahi\s+hai\b",
    r"\bfamily\s+problem\b",
    r"\bdeath\s+in\s+family\b",
    r"\bmaut\b",
]

_THIRD_PARTY_PATTERNS: list[str] = [
    r"\bpati\b",
    r"\bpathni\b",
    r"\bbhai\b",
    r"\bdidi\b",
    r"\bmaa\b",
    r"\bbaba\b",
    r"\bbeta\b",
    r"\bbeti\b",
    r"\bkoi\s+aur\s+baat\s+kar\b",
    r"\bsomeone\s+else\b",
    r"\bother\s+person\b",
]

_CONSENT_RISK_PATTERNS: list[str] = [
    r"\bband\s+karo\b",
    r"\bmat\s+karo\b",
    r"\brecording\s+band\b",
    r"\bstopped?\b",
    r"\bdo\s+not\s+call\b",
    r"\bdnc\b",
    r"\bno\s+consent\b",
    r"\bconsent\s+nahi\b",
    r"\bkoi\s+call\s+mat\b",
]

_RECORDING_OBJECTION_PATTERNS: list[str] = [
    r"\brecord\s+mat\s+karo\b",
    r"\bnahi\s+chahta\s+record\b",
    r"\bstop\s+recording\b",
    r"\bno\s+recording\b",
    r"\brecording\s+band\b",
]

_ELDERLY_VULNERABLE_PATTERNS: list[str] = [
    r"\bbhaiya\s+main\s+budhha\b",
    r"\bold\s+person\b",
    r"\bbujurg\b",
    r"\bpension\b",
    r"\bretired\b",
    r"\bwidow\b",
    r"\bvidhwa\b",
]

# Flags that require escalation_required = True (but not necessarily handoff)
_ESCALATION_FLAGS: frozenset[RiskFlag] = frozenset(
    {
        RiskFlag.ABUSE_DETECTED,
        RiskFlag.LEGAL_THREAT,
        RiskFlag.ESCALATION_TRIGGER,
        RiskFlag.REGULATORY_RISK,
    }
)


def _matches_any(text: str, patterns: list[str]) -> bool:
    lower = text.lower()
    return any(re.search(p, lower) for p in patterns)


class RiskEngine:
    """Evaluates TurnInput + optional EmotionSignal → RiskAssessment.

    All rules are deterministic. The engine applies pattern-matching rules
    in priority order and derives escalation decisions from the resulting
    flag set.

    Architecture: V2 Ch6.
    """

    def evaluate(
        self,
        turn: TurnInput,
        sentiment: Sentiment | None = None,
        stress_level: StressLevel | None = None,
    ) -> RiskAssessment:
        """Evaluate risk for the current turn.

        Args:
            turn: Finalized TurnInput from the Dialogue Manager.
            sentiment: Optional sentiment from EmotionIntelligenceEngine.
            stress_level: Optional stress level from EmotionIntelligenceEngine.

        Returns:
            RiskAssessment with active flags and escalation decisions.
        """
        text = turn.transcript
        flags: list[RiskFlag] = []

        # --- Detect flags in priority order ---

        if _matches_any(text, _ABUSE_PATTERNS) or sentiment == Sentiment.HOSTILE:
            flags.append(RiskFlag.ABUSE_DETECTED)

        if _matches_any(text, _LEGAL_THREAT_PATTERNS):
            flags.append(RiskFlag.LEGAL_THREAT)

        if _matches_any(text, _DISPUTE_PATTERNS):
            flags.append(RiskFlag.DISPUTE_CLAIM)

        if _matches_any(text, _HARDSHIP_PATTERNS):
            flags.append(RiskFlag.HARDSHIP_INDICATOR)

        if _matches_any(text, _ELDERLY_VULNERABLE_PATTERNS):
            flags.append(RiskFlag.ELDERLY_VULNERABLE)

        if _matches_any(text, _CONSENT_RISK_PATTERNS):
            flags.append(RiskFlag.CONSENT_RISK)

        if _matches_any(text, _RECORDING_OBJECTION_PATTERNS):
            flags.append(RiskFlag.RECORDING_OBJECTION)

        if _matches_any(text, _THIRD_PARTY_PATTERNS):
            flags.append(RiskFlag.THIRD_PARTY_ON_CALL)

        # Stress-based escalation trigger (critical stress even without explicit keywords)
        if stress_level == StressLevel.CRITICAL and RiskFlag.ABUSE_DETECTED not in flags:
            flags.append(RiskFlag.ESCALATION_TRIGGER)

        # --- Derive decisions ---
        human_handoff_required = RiskFlag.ABUSE_DETECTED in flags
        escalation_required = any(f in _ESCALATION_FLAGS for f in flags)

        logger.debug(
            "Risk evaluated",
            extra={
                "turn_id": turn.turn_id,
                "call_id": turn.call_id,
                "flags": [f.value for f in flags],
                "escalation_required": escalation_required,
                "human_handoff_required": human_handoff_required,
            },
        )

        return RiskAssessment(
            flags=flags,
            escalation_required=escalation_required,
            human_handoff_required=human_handoff_required,
        )
