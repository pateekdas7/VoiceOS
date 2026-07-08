"""RiskFlag enum — all flags the RiskEngine may raise.

Each flag represents a specific risk signal detected in the current turn.
Flags are evaluated deterministically by the RiskEngine; no LLM is involved.

Architecture: V2 Ch6 (Risk Engine).
"""

from __future__ import annotations

from enum import StrEnum


class RiskFlag(StrEnum):
    """Risk signals raised by the RiskEngine from TurnInput analysis.

    Flags are non-exclusive: multiple flags may be active in a single turn.
    The RiskAssessment aggregates active flags and derives escalation decisions.

    Architecture: V2 Ch6.
    """

    ESCALATION_TRIGGER = "ESCALATION_TRIGGER"
    """Customer language or intent indicates escalation is appropriate."""

    HARDSHIP_INDICATOR = "HARDSHIP_INDICATOR"
    """Customer signals financial or personal hardship."""

    ABUSE_DETECTED = "ABUSE_DETECTED"
    """Abusive or threatening language detected. Triggers immediate human handoff."""

    LEGAL_THREAT = "LEGAL_THREAT"
    """Customer threatens legal action, court, or consumer forum."""

    DISPUTE_CLAIM = "DISPUTE_CLAIM"
    """Customer explicitly disputes the debt or account details."""

    ELDERLY_VULNERABLE = "ELDERLY_VULNERABLE"
    """Signals that the customer may be elderly or otherwise vulnerable."""

    CONSENT_RISK = "CONSENT_RISK"
    """Customer raises concerns about consent or contact permission."""

    REGULATORY_RISK = "REGULATORY_RISK"
    """Call content creates potential RBI/DPDP regulatory exposure."""

    THIRD_PARTY_ON_CALL = "THIRD_PARTY_ON_CALL"
    """Evidence that a third party may be listening or has answered."""

    RECORDING_OBJECTION = "RECORDING_OBJECTION"
    """Customer objects to call recording."""
