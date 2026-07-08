"""Per-API-key rate limits, by subscription tier (V5 Ch16, V4 Ch12 §12.13, Sprint-025.md).

Requests/sec numbers are a Sprint-025 pick, not sourced from the
architecture (V4 Ch12 §12.13 names only a ``rate_limits.public_per_tenant_rps``
default of 100 -- see ``src.libs.api_security.rate_limiter.
DEFAULT_PUBLIC_PER_TENANT_RPS``) -- same "this sprint picks one
self-consistent number" precedent as Sprint-023's RBI/HITL-SLA constants and
Sprint-024's ``rate_card.py``.
"""

from __future__ import annotations

from src.libs.contracts.models.billing import SubscriptionTier

TIER_RPS_LIMITS: dict[SubscriptionTier, int] = {
    SubscriptionTier.TRIAL: 5,
    SubscriptionTier.STARTER: 20,
    SubscriptionTier.GROWTH: 100,
    SubscriptionTier.ENTERPRISE: 500,
    SubscriptionTier.ENTERPRISE_PLUS: 2000,
}

DEFAULT_RPS_LIMIT = 5
"""Fallback for a tenant with no billing subscription on record (defensive -- should not occur in practice)."""

BURST_MULTIPLIER = 3
"""Sprint-025 Part-3 "burst handling": a tenant may briefly spend up to 3x its sustained
rps within BURST_WINDOW_SECONDS -- matches migration 0025's seeded ``api_rate_limits``
burst_capacity, which is exactly 3x each tier's requests_per_second."""

BURST_WINDOW_SECONDS = 10
"""The longer sliding window the burst cap is checked against (vs. the 1s sustained-rps window)."""


def rps_for_tier(tier: SubscriptionTier | None) -> int:
    if tier is None:
        return DEFAULT_RPS_LIMIT
    return TIER_RPS_LIMITS.get(tier, DEFAULT_RPS_LIMIT)


def burst_for_tier(tier: SubscriptionTier | None) -> int:
    """Fallback burst cap when no persisted ``api_rate_limits`` row exists for this tier."""
    return rps_for_tier(tier) * BURST_MULTIPLIER


__all__ = [
    "BURST_MULTIPLIER",
    "BURST_WINDOW_SECONDS",
    "DEFAULT_RPS_LIMIT",
    "TIER_RPS_LIMITS",
    "burst_for_tier",
    "rps_for_tier",
]
