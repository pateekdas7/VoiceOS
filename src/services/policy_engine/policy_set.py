"""PolicySet — an ordered collection of PolicyRules bound to one scope.

Architecture: V4 Ch4 §4.13 (``scopes: [global, tenant, campaign]``).
"""

from __future__ import annotations

from dataclasses import dataclass

from .rule import PolicyRequest, PolicyRule


@dataclass(frozen=True)
class PolicySet:
    """A versioned set of rules applicable at one inheritance scope.

    Args:
        scope: 'global' | 'tenant' | 'campaign' (V4 Ch4 §4.13).
        scope_id: The tenant_id/campaign_id this set applies to, or ``None``
            for the global scope.
        rules: The rules in this set, evaluated in order.
        version: Policy-set version, carried into :class:`PolicyDecision`
            for audit (V4 Ch4 §4.6 ``policy_version``).
    """

    scope: str
    scope_id: str | None
    rules: tuple[PolicyRule, ...]
    version: str = "1"

    def matching_rules(self, request: PolicyRequest) -> tuple[PolicyRule, ...]:
        """Every rule in this set whose condition fires for ``request``."""
        return tuple(rule for rule in self.rules if rule.matches(request))
