"""SubscriptionManager — TRIAL/GROWTH/ENTERPRISE tier lifecycle (V5 Ch9, Sprint-024.md).

Tier features/limits are data (``rate_card.py``'s ``TIER_USAGE_LIMITS``),
not hardcoded per-tier if/else branches — enforcement itself always routes
through ``PolicyEngineService`` (``EntitlementEngine``), never through this
class directly (Sprint-024 DoD: "entitlement enforcement is performed
exclusively through PolicyEngine").

Architecture: V5 Ch9 (Billing Platform — SubscriptionManager).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Protocol

from src.libs.contracts.models.billing import BillingSubscription, SubscriptionTier
from src.libs.contracts.primitives import TenantId

from .rate_card import BASE_FEE_MINOR, RATE_CARD_VERSION, TRIAL_MAX_DAYS


class BillingRepositoryPort(Protocol):
    """Structural port over ``BillingRepository`` (create/get subscription)."""

    def create_subscription(self, subscription: BillingSubscription) -> BillingSubscription: ...

    def get_subscription(self, tenant_id: TenantId) -> BillingSubscription | None: ...


class SubscriptionManager:
    """Creates and inspects tenant billing subscriptions (V5 Ch9 SubscriptionManager)."""

    def __init__(self, repository: BillingRepositoryPort) -> None:
        self._repository = repository

    def create_subscription(
        self,
        tenant_id: TenantId,
        tier: SubscriptionTier,
        contract_start: datetime | None = None,
        contract_end: datetime | None = None,
        base_fee_minor: int | None = None,
        currency: str = "INR",
    ) -> BillingSubscription:
        """Create a tenant's subscription at the given tier (V5 Ch9 §9.2).

        ``contract_start`` defaults to now; ``base_fee_minor`` defaults to
        the tier's rate-card base fee (``rate_card.BASE_FEE_MINOR``) unless
        overridden (e.g. a negotiated ENTERPRISE contract amount).
        """
        now = contract_start or datetime.now(UTC)
        subscription = BillingSubscription(
            subscription_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            tier=tier,
            rate_card_version=RATE_CARD_VERSION,
            contract_start=now,
            contract_end=contract_end,
            base_fee_minor=base_fee_minor if base_fee_minor is not None else BASE_FEE_MINOR[tier],
            currency=currency,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        return self._repository.create_subscription(subscription)

    def get_subscription(self, tenant_id: TenantId) -> BillingSubscription | None:
        return self._repository.get_subscription(tenant_id)

    def is_trial_expired(self, subscription: BillingSubscription, as_of: datetime | None = None) -> bool:
        """Whether a TRIAL subscription's ``TRIAL_MAX_DAYS`` window has elapsed.

        Always ``False`` for non-TRIAL tiers — trial expiry is meaningless
        for GROWTH/ENTERPRISE subscriptions.
        """
        if subscription.tier != SubscriptionTier.TRIAL:
            return False
        now = as_of or datetime.now(UTC)
        contract_start = subscription.contract_start
        if contract_start.tzinfo is None:
            contract_start = contract_start.replace(tzinfo=UTC)
        return now >= contract_start + timedelta(days=TRIAL_MAX_DAYS)


__all__ = ["BillingRepositoryPort", "SubscriptionManager"]
