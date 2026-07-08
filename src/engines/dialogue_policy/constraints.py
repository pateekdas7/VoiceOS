"""PolicyConstraint enum — hard compliance constraints for agent dialogue.

These constraints are derived from RBI Fair Practice Code, DPDP Act, and
internal VoiceOS compliance rules. They are evaluated deterministically;
no LLM is involved. The full policy runtime lookup (per-tenant, per-campaign)
is implemented in Sprint-017; this sprint ships the core local rule set.

Architecture: V2 Ch8 (Dialogue Policy Engine).
"""

from __future__ import annotations

from enum import StrEnum


class PolicyConstraintType(StrEnum):
    """Hard compliance constraints the DialoguePolicyEngine may impose.

    Constraints that are added to ResponsePlan.must_say / must_not_say gate
    what the LLM renderer is permitted to produce. Violation of a hard
    constraint triggers re-generation or call termination.

    Architecture: V2 Ch8; V4 Ch4 (RBI/DPDP rules).
    """

    MUST_DISCLOSE_RECORDING = "MUST_DISCLOSE_RECORDING"
    """Agent must disclose call recording at the start of every call."""

    MUST_NOT_THREATEN = "MUST_NOT_THREATEN"
    """Agent must never threaten the customer with legal action, criminal complaint,
    or physical harm. Mandatory on every call (RBI FPC Clause 5)."""

    MUST_NOT_HARASS = "MUST_NOT_HARASS"
    """Agent must not make repeated, intimidating, or harassing contact."""

    MUST_VERIFY_IDENTITY_BEFORE_DISCLOSURE = "MUST_VERIFY_IDENTITY_BEFORE_DISCLOSURE"
    """Agent must verify the customer's identity before disclosing any account detail."""

    MUST_RESPECT_DND = "MUST_RESPECT_DND"
    """Agent must honour Do-Not-Disturb registry status; no contact outside consent window."""

    MUST_REFERENCE_DPD_CORRECTLY = "MUST_REFERENCE_DPD_CORRECTLY"
    """When referencing overdue status, agent must use the factual DPD figure from
    CustomerContext — never an invented or rounded value (RI-5)."""

    MUST_NOT_MISREPRESENT_AMOUNT = "MUST_NOT_MISREPRESENT_AMOUNT"
    """Agent must never state an amount that differs from the authoritative outstanding
    balance in CustomerContext (RI-5, Law of Authority)."""
