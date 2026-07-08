"""Prometheus metrics for AI Governance (V4 Ch3 §"Observability").

Architecture: V4 Ch3 (AI Governance); Sprint-018 AC (governance_verdicts_by_outcome,
law_of_authority_violations).
"""

from __future__ import annotations

from prometheus_client import Counter

GOVERNANCE_VERDICTS_BY_OUTCOME: Counter = Counter(
    "voiceos_governance_verdicts_by_outcome_total",
    "Total AI Governance verdicts issued, by outcome.",
    labelnames=["outcome"],
)
"""Counter: one increment per GovernanceLayer.evaluate() call, labeled APPROVE/REQUIRE_HUMAN/BLOCK."""

LAW_OF_AUTHORITY_VIOLATIONS: Counter = Counter(
    "voiceos_law_of_authority_violations_total",
    "Total Law of Authority violations detected (BLOCK verdicts caused by an ungrounded fact).",
)
"""Counter: increments only when the LawOfAuthorityChecker itself finds an ungrounded fact —
not every BLOCK verdict (a policy-driven or content-moderation BLOCK does not increment this)."""


def record_verdict(outcome: str) -> None:
    """Increment the verdicts-by-outcome counter."""
    GOVERNANCE_VERDICTS_BY_OUTCOME.labels(outcome=outcome).inc()


def record_law_of_authority_violation() -> None:
    """Increment the Law of Authority violation counter."""
    LAW_OF_AUTHORITY_VIOLATIONS.inc()
