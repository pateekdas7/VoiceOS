"""UsageLimitEnforcer — real-time entitlement blocking (V5 Ch10, Sprint-024.md).

Redis fast-path counters (per Sprint-024.md: "Sums current period usage from
Redis + validates against entitlement") plus the same
``EntitlementEngine``/``PolicyEngineService`` PDP call ``BillingService``
uses — so a live per-call usage check and a monthly invoice reconciliation
agree on exactly the same tier limits, never a second, divergent copy of
the limit table.

Not atomic under concurrent writers (the fixture/real Redis client this
sprint targets exposes only ``incr(key)`` — increment-by-1 — not
``incrby``): acceptable because Postgres ``usage_events`` remains the
authoritative ledger (``UsageAggregator``/``InvoiceGenerator`` never read
these Redis counters) — this is purely an accelerator for the "should I
block this call right now" check (V5 Ch10 perf target: <5ms).

Architecture: V5 Ch10 (Usage Metering — UsageLimitEnforcer).
"""

from __future__ import annotations

from typing import Any

from src.libs.contracts.models.billing import SubscriptionTier, UsageType
from src.libs.redis_client.ttl_guard import TTLGuard

from ..billing.entitlement import EntitlementEngine
from .metrics import record_enforcement

DEFAULT_COUNTER_TTL_SECONDS = 31 * 24 * 3600
"""A little over a month — current-period counters are reset by the next
period's fresh key (``period_key`` is part of the Redis key), so this TTL
only bounds worst-case staleness if a key is ever orphaned."""


def _redis_key(tenant_id: str, usage_type: UsageType, period_key: str) -> str:
    return f"usage:{tenant_id}:{usage_type.value}:{period_key}"


class UsageLimitEnforcer:
    """Blocks a billable operation once the tenant's tier limit is reached.

    ``redis`` is untyped (``Any``), matching the ``PolicyEngine``/``TTLGuard``
    precedent — a real ``redis.Redis`` client's ``get``/``set`` signatures are
    strictly wider than any Protocol this module could declare (extra
    parameters, broader return types), so a structural Protocol would reject
    the real client while only ever satisfying ``FakeRedisClient``.
    """

    def __init__(self, redis: Any, entitlement_engine: EntitlementEngine) -> None:
        self._redis = redis
        self._ttl_guard = TTLGuard(redis)
        self._entitlements = entitlement_engine

    def current_usage(self, tenant_id: str, usage_type: UsageType, period_key: str) -> int:
        raw = self._redis.get(_redis_key(tenant_id, usage_type, period_key))
        if raw is None:
            return 0
        return int(raw.decode() if isinstance(raw, bytes) else raw)

    def check_and_allow(
        self,
        tenant_id: str,
        tier: SubscriptionTier,
        usage_type: UsageType,
        quantity: int,
        period_key: str,
        trial_expired: bool = False,
    ) -> bool:
        """Whether ``quantity`` more units of ``usage_type`` may be consumed right now.

        On PERMIT, atomically (from this process's perspective —
        get-then-set, see module docstring) advances the Redis counter by
        ``quantity`` so the next check sees the updated running total.
        """
        current = self.current_usage(tenant_id, usage_type, period_key)
        projected = current + quantity
        decision = self._entitlements.check_usage(tenant_id, tier, usage_type, projected, trial_expired)
        if decision.outcome.value != "PERMIT":
            record_enforcement(allowed=False)
            return False

        key = _redis_key(tenant_id, usage_type, period_key)
        self._ttl_guard.set(key, str(projected), ex=DEFAULT_COUNTER_TTL_SECONDS)
        record_enforcement(allowed=True)
        return True


__all__ = ["DEFAULT_COUNTER_TTL_SECONDS", "UsageLimitEnforcer"]
