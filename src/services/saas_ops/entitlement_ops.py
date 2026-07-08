"""EntitlementOpsService -- license enforcement + entitlement audit (V5 Ch23).

A thin operational wrapper over the Billing Platform's existing
``EntitlementEngine``/``PolicyEngineService.check_entitlement()`` (Sprint-024)
-- this service never re-implements entitlement logic, it only exposes an
operations-facing view: "is this tenant currently within its license" and
"what's the audit trail for that answer," reusing the same PDP-routed
decision every billing-path check already goes through (Law of Authority:
business logic never lives outside the Policy Engine).

Architecture: V5 Ch23 (SaaS Operations Platform -- License Entitlement Enforcement).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from src.libs.contracts.models.saas_ops import EntitlementAuditResult
from src.libs.contracts.primitives import TenantId
from src.services.policy_engine.decision import PolicyOutcome

from . import metrics


class EntitlementCheckPort(Protocol):
    """Structural port over ``PolicyEngineService.check_entitlement()``."""

    def check_entitlement(
        self,
        tenant_id: str,
        feature: str,
        tier: str,
        usage_quantity: int | None = None,
        usage_limit: int | None = None,
        trial_expired: bool = False,
        subject: str = "saas_ops",
    ) -> object: ...


class EntitlementOpsService:
    """Operational license-enforcement + entitlement-audit surface for SaaS Ops."""

    def __init__(self, policy_engine: EntitlementCheckPort | None = None) -> None:
        self._policy_engine = policy_engine

    def check_license(self, tenant_id: TenantId, tier: str, *, trial_expired: bool = False) -> bool:
        """Return whether ``tenant_id`` is currently within its license entitlement.

        Without a wired Policy Engine, defaults to permissive (True) -- same
        "optional collaborator, None preserves prior behavior" precedent as
        every other Sprint-016+ optional dependency; a real deployment always
        wires this in.
        """
        if self._policy_engine is None:
            return True
        decision = self._policy_engine.check_entitlement(
            str(tenant_id), "license", tier, trial_expired=trial_expired, subject="saas_ops_entitlement_check"
        )
        outcome = getattr(decision, "outcome", PolicyOutcome.PERMIT)
        within = outcome == PolicyOutcome.PERMIT
        metrics.record_entitlement_check(within=within)
        return within

    def audit_entitlements(
        self, tenant_id: TenantId, tier: str, *, trial_expired: bool = False
    ) -> EntitlementAuditResult:
        """Return a point-in-time entitlement audit snapshot for ``tenant_id``."""
        within = self.check_license(tenant_id, tier, trial_expired=trial_expired)
        return EntitlementAuditResult(
            tenant_id=tenant_id,
            plan_tier=tier,
            within_entitlement=within,
            reason="" if within else "license entitlement check returned DENY",
            checked_at=datetime.now(UTC),
        )


__all__ = ["EntitlementCheckPort", "EntitlementOpsService"]
