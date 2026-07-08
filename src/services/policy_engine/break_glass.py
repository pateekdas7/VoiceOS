"""BreakGlassPolicy — time-boxed, dual-approval emergency override (V4 Ch4 §4.12).

"time-boxed, heavily-audited override policies for incidents ... requiring
elevated approval and auto-expiring; every break-glass use is a high-severity
audit event." The caller (PolicyEngine.emergency_override) is responsible for
always emitting the audit event, regardless of the outcome returned here —
including a *denied* break-glass attempt, which is itself a security signal.

Architecture: V4 Ch4 §4.12, §4.13 (``emergency: {break_glass: true,
max_ttl_min: 60, requires_approval: 2}``), §4.18.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .decision import PolicyDecision, PolicyOutcome

DEFAULT_REQUIRED_APPROVALS = 2
DEFAULT_MAX_TTL_MINUTES = 60

BREAK_GLASS_RULE_PREFIX = "BREAK-GLASS"


@dataclass(frozen=True)
class BreakGlassDirective:
    """A request to override normal policy evaluation for one rule/action."""

    rule_id: str
    reason: str
    requested_by: str
    approvers: tuple[str, ...] = field(default_factory=tuple)
    requested_at: datetime = field(default_factory=datetime.utcnow)


class BreakGlassPolicy:
    """Evaluates emergency override directives under dual-approval + TTL rules."""

    def __init__(
        self,
        required_approvals: int = DEFAULT_REQUIRED_APPROVALS,
        max_ttl_minutes: int = DEFAULT_MAX_TTL_MINUTES,
    ) -> None:
        self._required_approvals = required_approvals
        self._max_ttl = timedelta(minutes=max_ttl_minutes)

    def authorize(self, directive: BreakGlassDirective, *, now: datetime | None = None) -> PolicyDecision:
        """Authorize (or deny) a break-glass directive.

        Denies when: fewer than ``required_approvals`` distinct approvers, or
        the directive has exceeded ``max_ttl_minutes`` since it was requested.
        """
        now = now if now is not None else datetime.utcnow()
        distinct_approvers = set(directive.approvers)

        if len(distinct_approvers) < self._required_approvals:
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                matching_rules=(f"{BREAK_GLASS_RULE_PREFIX}:{directive.rule_id}",),
                reason=(
                    f"break-glass denied: {len(distinct_approvers)}/{self._required_approvals} "
                    "required approvals present"
                ),
            )

        if now - directive.requested_at > self._max_ttl:
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                matching_rules=(f"{BREAK_GLASS_RULE_PREFIX}:{directive.rule_id}",),
                reason=f"break-glass denied: directive expired (max_ttl={self._max_ttl})",
            )

        return PolicyDecision(
            outcome=PolicyOutcome.PERMIT,
            matching_rules=(f"{BREAK_GLASS_RULE_PREFIX}:{directive.rule_id}",),
            reason=(
                f"break-glass override by {sorted(distinct_approvers)} for '{directive.rule_id}': {directive.reason}"
            ),
            obligations=("log", "require_human_review"),
        )
