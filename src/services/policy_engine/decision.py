"""PolicyOutcome and PolicyDecision — the Policy Engine's output vocabulary.

Architecture: V4 Ch4 §4.6 (Outputs), §4.12 (unified DSL: PERMIT/DENY +
REQUIRE/FORBID, deny-overrides-permit precedence).
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class PolicyOutcome(StrEnum):
    """The unified Policy DSL effect vocabulary (V4 Ch4 §4.12).

    ``PERMIT``/``DENY`` are the authorization-domain effects; ``REQUIRE``/
    ``FORBID`` are the compliance/conversational-domain effects reused from
    the Volume 2 Chapter 8 Policy DSL. All four combine under one
    deny-overrides precedence order when multiple rules match a single
    request.
    """

    PERMIT = "PERMIT"
    DENY = "DENY"
    REQUIRE = "REQUIRE"
    FORBID = "FORBID"


# Deny-overrides precedence (V4 Ch4 §4.12): "DENY/FORBID overrides PERMIT";
# FORBID is the strictest (an absolute prohibition), then DENY, then REQUIRE
# (an obligation gate), then PERMIT (the default/weakest outcome).
_PRECEDENCE: dict[PolicyOutcome, int] = {
    PolicyOutcome.FORBID: 3,
    PolicyOutcome.DENY: 2,
    PolicyOutcome.REQUIRE: 1,
    PolicyOutcome.PERMIT: 0,
}


def most_restrictive(outcomes: Iterable[PolicyOutcome]) -> PolicyOutcome:
    """Combine multiple matched-rule outcomes under deny-overrides precedence.

    Returns ``PolicyOutcome.PERMIT`` when ``outcomes`` is empty (no rule
    matched — the safe default for a request no policy pack governs).
    """
    best = PolicyOutcome.PERMIT
    for outcome in outcomes:
        if _PRECEDENCE[outcome] > _PRECEDENCE[best]:
            best = outcome
    return best


class PolicyDecision(BaseModel):
    """The result of a single :class:`~src.services.policy_engine.engine.PolicyEngine`
    evaluation (V4 Ch4 §4.6).

    ``matching_rules`` lists the stable rule IDs that fired (in evaluation
    order); ``reason`` is the human-readable audit rationale.
    """

    model_config = ConfigDict(frozen=True)

    outcome: PolicyOutcome
    matching_rules: tuple[str, ...] = ()
    reason: str = ""
    policy_version: str = "1"
    obligations: tuple[str, ...] = ()
    """e.g. ('redact', 'log', 'require_mfa', 'require_human') — V4 Ch4 §4.6."""
