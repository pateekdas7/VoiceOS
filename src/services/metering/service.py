"""MeteringService — facade over collection, aggregation, and limit enforcement (V5 Ch10).

Architecture: V5 Ch10 (Usage Metering Platform).
"""

from __future__ import annotations

from src.libs.contracts.models.billing import SubscriptionTier, UsageType
from src.libs.contracts.primitives import TenantId

from .aggregator import UsageAggregator
from .collector import ConsumerPort, UsageCollector
from .enforcer import UsageLimitEnforcer


class MeteringService:
    """Facade composing ``UsageCollector``/``UsageAggregator``/``UsageLimitEnforcer``."""

    def __init__(
        self,
        collector: UsageCollector,
        aggregator: UsageAggregator,
        enforcer: UsageLimitEnforcer,
    ) -> None:
        self._collector = collector
        self._aggregator = aggregator
        self._enforcer = enforcer

    def start_collecting(self, consumer: ConsumerPort) -> None:
        """Wire the collector's handlers onto a live ``Consumer``."""
        self._collector.register(consumer)

    @property
    def aggregator(self) -> UsageAggregator:
        return self._aggregator

    def check_and_allow(
        self,
        tenant_id: TenantId,
        tier: SubscriptionTier,
        usage_type: UsageType,
        quantity: int,
        period_key: str,
        trial_expired: bool = False,
    ) -> bool:
        """Real-time entitlement check — False means the caller must return 429."""
        return self._enforcer.check_and_allow(tenant_id, tier, usage_type, quantity, period_key, trial_expired)


__all__ = ["MeteringService"]
