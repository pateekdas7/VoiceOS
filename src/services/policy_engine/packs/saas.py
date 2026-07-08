"""SaaSPolicyPack — tenant-lifecycle admission rules (V5 Ch2/Ch3, Sprint-021).

A suspended/cancelled/deleted tenant must not be able to admit new calls —
this pack expresses that as a PDP rule so ``TenantSuspender`` (Sprint-021)
plugs into the same PERMIT/DENY DSL every other domain uses, rather than a
bespoke ad hoc check bypassing the PDP.

Context keys consumed:
    tenant_active (bool): whether the tenant is currently in PRODUCTION
        status. Supplied by the caller (``PolicyEngineService.check_tenant_active``)
        since the Policy Engine itself has no direct dependency on
        ``src.services.tenant_management`` (services do not import each
        other's concrete state — the caller resolves the fact first, same
        precedent as ``calls_today_count`` in ``check_call_admission``).

Architecture: V5 Ch2 (Multi-Tenant Architecture), V5 Ch3 (Tenant Lifecycle).
"""

from __future__ import annotations

from ..decision import PolicyOutcome
from ..rule import PolicyCondition, PolicyEffect, PolicyRequest, PolicyRule


def _tenant_not_active(request: PolicyRequest) -> bool:
    return request.context.get("tenant_active", True) is False


class SaaSPolicyPack:
    """Tenant-lifecycle admission rules (V5 Ch2/Ch3)."""

    TENANT_MUST_BE_ACTIVE: PolicyRule = PolicyRule(
        rule_id="SAAS-TENANT-MUST-BE-ACTIVE",
        pack="saas",
        domain="tenant",
        condition=PolicyCondition(
            "tenant is not in PRODUCTION status",
            _tenant_not_active,
        ),
        effect=PolicyEffect(PolicyOutcome.DENY),
        description="A suspended/cancelled/deleting/deleted tenant may not admit new calls.",
        hard_rule=True,
    )

    ALL_RULES: tuple[PolicyRule, ...] = (TENANT_MUST_BE_ACTIVE,)

    @classmethod
    def rules(cls) -> tuple[PolicyRule, ...]:
        """All SaaS tenant-lifecycle rules, for registration into a PolicyEngine's rule registry."""
        return cls.ALL_RULES
