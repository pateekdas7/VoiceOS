"""EntitlementEngine — feature-access checks via PolicyEngine (V5 Ch9, Sprint-024.md).

Every feature-access/usage-limit check is expressed as a
``PolicyEngineService.check_entitlement()`` call (``BillingPolicyPack``) —
this class never decides PERMIT/DENY itself, it only resolves the facts
(current usage, the tier's limit, trial expiry) the PDP's rule conditions
read. This is the mechanical meaning of Sprint-024's DoD item "entitlement
enforcement is performed exclusively through PolicyEngine."

Architecture: V5 Ch9 (Billing Platform — EntitlementEngine).
"""

from __future__ import annotations

from typing import Protocol

from src.libs.contracts.models.billing import SubscriptionTier, UsageType
from src.services.policy_engine.decision import PolicyDecision, PolicyOutcome

from .rate_card import TIER_USAGE_LIMITS


class PolicyEntitlementPort(Protocol):
    """Structural port over ``PolicyEngineService.check_entitlement()``."""

    def check_entitlement(
        self,
        tenant_id: str,
        feature: str,
        tier: str,
        usage_quantity: int | None = None,
        usage_limit: int | None = None,
        trial_expired: bool = False,
        subject: str = "billing_service",
    ) -> PolicyDecision: ...


class EntitlementEngine:
    """Resolves usage-limit facts and routes every check through the PDP."""

    def __init__(self, policy_engine: PolicyEntitlementPort) -> None:
        self._policy_engine = policy_engine

    def limit_for(self, tier: SubscriptionTier, usage_type: UsageType) -> int | None:
        """The tier's configured limit for ``usage_type`` (``None`` = unlimited)."""
        return TIER_USAGE_LIMITS.get(tier, {}).get(usage_type)

    def check_usage(
        self,
        tenant_id: str,
        tier: SubscriptionTier,
        usage_type: UsageType,
        current_period_quantity: int,
        trial_expired: bool = False,
    ) -> PolicyDecision:
        """Evaluate whether ``current_period_quantity`` may still grow for ``usage_type``.

        DENY means the tier's usage limit for ``usage_type`` has been
        reached (or the TRIAL window has expired) — the caller
        (``UsageLimitEnforcer``) is responsible for turning that into a 429.
        """
        limit = self.limit_for(tier, usage_type)
        return self._policy_engine.check_entitlement(
            tenant_id=tenant_id,
            feature=usage_type.value,
            tier=tier.value,
            usage_quantity=current_period_quantity,
            usage_limit=limit,
            trial_expired=trial_expired,
        )

    def is_permitted(
        self,
        tenant_id: str,
        tier: SubscriptionTier,
        usage_type: UsageType,
        current_period_quantity: int,
        trial_expired: bool = False,
    ) -> bool:
        decision = self.check_usage(tenant_id, tier, usage_type, current_period_quantity, trial_expired)
        return decision.outcome == PolicyOutcome.PERMIT


__all__ = ["EntitlementEngine", "PolicyEntitlementPort"]
