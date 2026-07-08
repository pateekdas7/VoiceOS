"""AIGovernancePolicyPack — Law of Authority guardrails (V4 Ch3 AI Governance).

Encodes the CLAUDE.md/architecture "Law of Authority" as PDP rules: the LLM
never invents facts, authoritative data always wins over derived state, and
high-risk decisions require human review before being acted on.

Context keys consumed:
    contains_unverified_fact_claim (bool): output evaluator flagged an unverifiable claim.
    derived_state_conflicts_authoritative (bool): derived state disagrees with the
        authoritative system of record.
    risk_score (float): RiskEngine's risk score for this turn, 0.0-1.0.
    human_review_threshold (float): tenant-configured threshold above which
        human review is mandatory. Default 0.9.

Architecture: V4 Ch3 (AI Governance); V4 Ch4; Law of Authority (CLAUDE.md).
"""

from __future__ import annotations

from ..decision import PolicyOutcome
from ..rule import PolicyCondition, PolicyEffect, PolicyRequest, PolicyRule

DEFAULT_HUMAN_REVIEW_THRESHOLD = 0.9


def _unverified_fact_claim(request: PolicyRequest) -> bool:
    return bool(request.context.get("contains_unverified_fact_claim", False))


def _derived_conflicts_authoritative(request: PolicyRequest) -> bool:
    return bool(request.context.get("derived_state_conflicts_authoritative", False))


def _high_risk_requires_human(request: PolicyRequest) -> bool:
    threshold = request.context.get("human_review_threshold", DEFAULT_HUMAN_REVIEW_THRESHOLD)
    return bool(request.context.get("risk_score", 0.0) >= threshold)


class AIGovernancePolicyPack:
    """Law of Authority guardrail rules (V4 Ch3, V4 Ch4)."""

    NO_HALLUCINATED_FACTS: PolicyRule = PolicyRule(
        rule_id="AIGOV-NO-HALLUCINATED-FACTS",
        pack="ai_governance",
        domain="ai_governance",
        condition=PolicyCondition(
            "LLM output contains an unverifiable factual claim",
            _unverified_fact_claim,
        ),
        effect=PolicyEffect(PolicyOutcome.FORBID),
        description="Law of Authority: the LLM must never invent facts.",
        hard_rule=True,
    )

    AUTHORITATIVE_DATA_WINS: PolicyRule = PolicyRule(
        rule_id="AIGOV-AUTHORITATIVE-DATA-WINS",
        pack="ai_governance",
        domain="ai_governance",
        condition=PolicyCondition(
            "derived state conflicts with the authoritative system of record",
            _derived_conflicts_authoritative,
        ),
        effect=PolicyEffect(PolicyOutcome.FORBID),
        description="Law of Authority: derived state must never overwrite or contradict authoritative state.",
        hard_rule=True,
    )

    HUMAN_REVIEW_FOR_HIGH_RISK: PolicyRule = PolicyRule(
        rule_id="AIGOV-HUMAN-REVIEW-HIGH-RISK",
        pack="ai_governance",
        domain="ai_governance",
        condition=PolicyCondition(
            "risk_score at or above the human-review threshold",
            _high_risk_requires_human,
        ),
        effect=PolicyEffect(PolicyOutcome.REQUIRE, obligations=("require_human",)),
        description="High-risk turns must be routed for human review before automated action proceeds.",
        hard_rule=True,
    )

    ALL_RULES: tuple[PolicyRule, ...] = (
        NO_HALLUCINATED_FACTS,
        AUTHORITATIVE_DATA_WINS,
        HUMAN_REVIEW_FOR_HIGH_RISK,
    )

    @classmethod
    def rules(cls) -> tuple[PolicyRule, ...]:
        """All AI-governance rules, for registration into a PolicyEngine's rule registry."""
        return cls.ALL_RULES
