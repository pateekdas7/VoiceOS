"""ConversationalPolicyPack — what may be said per context (V2 Ch8 Policy DSL).

Volume 4 Ch4 hosts the Volume 2 Chapter 8 conversational Policy DSL as one
domain of the enterprise Policy Engine ("the deny-overrides-permit
precedence mirrors the Vol 2 Ch 1 Four-Class Hierarchy"). This pack
generalizes the always-applicable local rules already enforced by
:class:`~src.engines.dialogue_policy.engine.DialoguePolicyEngine`
(Sprint-011) into PDP rules so they can be centrally versioned, cached, and
audited like every other domain.

Context keys consumed:
    utterance_classification (str): e.g. 'threatening', 'harassing', 'neutral'.
    turn_index (int): zero-based turn index (0 = call start).
    purpose_disclosed (bool): whether the call's purpose has been stated.

Architecture: V2 Ch8 (Conversational Policy DSL); V4 Ch4.
"""

from __future__ import annotations

from ..decision import PolicyOutcome
from ..rule import PolicyCondition, PolicyEffect, PolicyRequest, PolicyRule


def _threatening_utterance(request: PolicyRequest) -> bool:
    return request.context.get("utterance_classification") == "threatening"


def _harassing_utterance(request: PolicyRequest) -> bool:
    return request.context.get("utterance_classification") == "harassing"


def _purpose_not_disclosed_at_start(request: PolicyRequest) -> bool:
    return request.context.get("turn_index", 0) == 0 and not request.context.get("purpose_disclosed", False)


class ConversationalPolicyPack:
    """Conversational must-say/must-not-say rules (V2 Ch8, V4 Ch4)."""

    MUST_NOT_THREATEN: PolicyRule = PolicyRule(
        rule_id="CONV-MUST-NOT-THREATEN",
        pack="conversational",
        domain="conversational",
        condition=PolicyCondition(
            "utterance classified as threatening",
            _threatening_utterance,
        ),
        effect=PolicyEffect(PolicyOutcome.FORBID),
        description="RBI FPC Clause 5: the agent must never threaten the customer.",
        hard_rule=True,
    )

    MUST_NOT_HARASS: PolicyRule = PolicyRule(
        rule_id="CONV-MUST-NOT-HARASS",
        pack="conversational",
        domain="conversational",
        condition=PolicyCondition(
            "utterance classified as harassing",
            _harassing_utterance,
        ),
        effect=PolicyEffect(PolicyOutcome.FORBID),
        description="RBI FPC: the agent must not make repeated, intimidating, or harassing statements.",
        hard_rule=True,
    )

    MUST_DISCLOSE_PURPOSE_AT_START: PolicyRule = PolicyRule(
        rule_id="CONV-MUST-DISCLOSE-PURPOSE",
        pack="conversational",
        domain="conversational",
        condition=PolicyCondition(
            "call purpose not yet disclosed at call start",
            _purpose_not_disclosed_at_start,
        ),
        effect=PolicyEffect(PolicyOutcome.REQUIRE, obligations=("disclose_call_purpose",)),
        description="The call's purpose must be disclosed at call start.",
        hard_rule=True,
    )

    ALL_RULES: tuple[PolicyRule, ...] = (
        MUST_NOT_THREATEN,
        MUST_NOT_HARASS,
        MUST_DISCLOSE_PURPOSE_AT_START,
    )

    @classmethod
    def rules(cls) -> tuple[PolicyRule, ...]:
        """All conversational rules, for registration into a PolicyEngine's rule registry."""
        return cls.ALL_RULES
