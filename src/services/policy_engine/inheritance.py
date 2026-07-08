"""PolicyInheritance — global → tenant → campaign rule composition (V4 Ch4 §4.12).

"more-specific scopes refine, never *weaken* a security/compliance hard
rule (a tenant cannot opt out of DPDP)" — enforced structurally here by
composing rule sets additively: a more-specific scope's rules are appended
to, never substituted for, the broader scope's rules. Nothing in this
engine removes an inherited rule, so hard rules (DPDP/RBI/AI-governance) can
never be dropped by a tenant- or campaign-level PolicySet.
"""

from __future__ import annotations

from .policy_set import PolicySet
from .rule import PolicyRule


class PolicyInheritance:
    """Resolves the effective rule list for a request's scope chain."""

    @staticmethod
    def resolve(
        global_set: PolicySet | None,
        tenant_set: PolicySet | None = None,
        campaign_set: PolicySet | None = None,
    ) -> tuple[PolicyRule, ...]:
        """Compose global + tenant + campaign rules, most-general first.

        Any of the three may be ``None`` (e.g. no campaign scope on this
        request) — absent scopes simply contribute no rules.
        """
        rules: list[PolicyRule] = []
        for policy_set in (global_set, tenant_set, campaign_set):
            if policy_set is not None:
                rules.extend(policy_set.rules)
        return tuple(rules)
