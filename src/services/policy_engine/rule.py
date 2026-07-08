"""PolicyRequest, PolicyCondition, PolicyEffect, PolicyRule — the Policy Engine's
input and rule vocabulary (V4 Ch4 §4.5, §4.7).

Architecture: V4 Ch4 (Policy Engine Architecture).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .decision import PolicyOutcome


@dataclass(frozen=True)
class PolicyRequest:
    """A single policy evaluation request (V4 Ch4 §4.5/§4.7 ``evaluate(...)`` args).

    ``context`` carries the facts a rule's condition inspects (e.g.
    ``hour``, ``calls_today_count``, ``identity_verified``,
    ``has_consent``) — deliberately an untyped mapping because each policy
    pack's rules read a different subset of facts; the pack modules document
    the keys they consume.
    """

    domain: str
    """Policy domain: 'authz' | 'rbi' | 'dpdp' | 'ai_governance' | 'conversational'."""

    action: str
    """The action being evaluated (e.g. 'start_call', 'disclose_debt', 'process_customer_data')."""

    subject: str
    """The authenticated principal (agent/service/tenant) performing the action."""

    resource: str
    """The resource being acted upon (e.g. a call_id, customer_id, campaign_id)."""

    tenant_id: str | None = None
    """Tenant scope — selects which tenant-level PolicySet applies, if any (AR-8)."""

    campaign_id: str | None = None
    """Campaign scope — selects which campaign-level PolicySet applies, if any."""

    context: dict[str, Any] = field(default_factory=dict)
    """Request-specific facts consulted by rule conditions. See each pack module
    for the keys it reads."""


PolicyConditionFn = Callable[[PolicyRequest], bool]


@dataclass(frozen=True)
class PolicyCondition:
    """A named, evaluable predicate over a :class:`PolicyRequest`."""

    description: str
    predicate: PolicyConditionFn

    def evaluate(self, request: PolicyRequest) -> bool:
        return self.predicate(request)


@dataclass(frozen=True)
class PolicyEffect:
    """The outcome + obligations a rule produces when its condition matches."""

    outcome: PolicyOutcome
    obligations: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyRule:
    """One policy rule: a named condition bound to an effect (V4 Ch4 §4.12).

    ``hard_rule=True`` marks rules that are structurally unweakenable by a
    lower (tenant/campaign) scope — DPDP, RBI, Law-of-Authority, and
    encryption rules per V4 Ch4 §4.18/§4.13 ``hard_rules_unweakenable``.
    Rules are additive-only in this engine (no scope may remove or override
    a rule it inherits — see :mod:`inheritance`), so ``hard_rule`` is
    documentation/audit metadata rather than an enforcement mechanism in its
    own right.
    """

    rule_id: str
    pack: str
    domain: str
    condition: PolicyCondition
    effect: PolicyEffect
    description: str = ""
    hard_rule: bool = False

    def matches(self, request: PolicyRequest) -> bool:
        """Whether this rule's condition fires for ``request``."""
        return self.condition.evaluate(request)

    def decide(self, request: PolicyRequest) -> PolicyOutcome:
        """This rule's outcome for ``request``: its effect if matched, else PERMIT."""
        return self.effect.outcome if self.matches(request) else PolicyOutcome.PERMIT
