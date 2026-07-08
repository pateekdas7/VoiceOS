"""AuthorizationPolicyPack — RBAC / tenant-isolation authorization rules (V4 Ch6).

Full RBAC (roles, permissions, JIT elevation, approval workflows) is
Sprint-018 scope (V4 Ch6). This pack ships the two authorization-domain
rules the PDP must already enforce wherever it is consulted this sprint:
deny-by-default on a missing grant, and the tenant-isolation hard rule
(AR-8) — both expressed in the same unified PERMIT/DENY DSL as every other
pack so they compose identically under deny-overrides.

Context keys consumed:
    granted_permissions (tuple[str, ...]): permissions explicitly granted to the subject.
    resource_tenant_id (str | None): the tenant_id owning ``request.resource``, if scoped.

Architecture: V4 Ch6 (Authorization/RBAC); V4 Ch4; AR-8 (tenant isolation).
"""

from __future__ import annotations

from ..decision import PolicyOutcome
from ..rule import PolicyCondition, PolicyEffect, PolicyRequest, PolicyRule


def _permission_not_granted(request: PolicyRequest) -> bool:
    return request.action not in request.context.get("granted_permissions", ())


def _cross_tenant_access(request: PolicyRequest) -> bool:
    resource_tenant_id = request.context.get("resource_tenant_id")
    return resource_tenant_id is not None and resource_tenant_id != request.tenant_id


class AuthorizationPolicyPack:
    """RBAC deny-by-default and tenant-isolation rules (V4 Ch6, V4 Ch4)."""

    PERMISSION_REQUIRED: PolicyRule = PolicyRule(
        rule_id="AUTHZ-PERMISSION-REQUIRED",
        pack="authorization",
        domain="authz",
        condition=PolicyCondition(
            "subject lacks an explicit grant for the requested action",
            _permission_not_granted,
        ),
        effect=PolicyEffect(PolicyOutcome.DENY),
        description="Deny-by-default: an action is only PERMITted with an explicit granted permission.",
        hard_rule=False,
    )

    TENANT_ISOLATION: PolicyRule = PolicyRule(
        rule_id="AUTHZ-TENANT-ISOLATION",
        pack="authorization",
        domain="authz",
        condition=PolicyCondition(
            "resource belongs to a different tenant than the requesting subject",
            _cross_tenant_access,
        ),
        effect=PolicyEffect(PolicyOutcome.FORBID),
        description="AR-8: cross-tenant resource access is absolutely prohibited.",
        hard_rule=True,
    )

    ALL_RULES: tuple[PolicyRule, ...] = (PERMISSION_REQUIRED, TENANT_ISOLATION)

    @classmethod
    def rules(cls) -> tuple[PolicyRule, ...]:
        """All authorization rules, for registration into a PolicyEngine's rule registry."""
        return cls.ALL_RULES
