"""APIRateLimiter — per-tenant/per-user API rate limiting (V4 Ch12 §12.12).

Thin wrapper over the Sprint-013 :class:`~src.libs.redis_client.rate_limiter.RateLimiter`
— API security reuses the same sliding-window primitive rather than a
parallel implementation (V4 Ch12 §12.2 "reusing Vol 3 Ch 4/14").

Architecture: V4 Ch12 (API Security) §12.12 (rate limiting & quotas).
"""

from __future__ import annotations

from src.libs.redis_client.rate_limiter import RateLimiter, RateLimitResult

DEFAULT_PUBLIC_PER_TENANT_RPS = 100
"""V4 Ch12 §12.13 default ``rate_limits.public_per_tenant_rps``."""


class APIRateLimiter:
    """Per-tenant (optionally per-user) API-level rate limiter."""

    def __init__(self, rate_limiter: RateLimiter, requests_per_second: int = DEFAULT_PUBLIC_PER_TENANT_RPS) -> None:
        self._rate_limiter = rate_limiter
        self._rps = requests_per_second

    def check(self, tenant_id: str, user_id: str = "") -> RateLimitResult:
        """Check (and consume, if allowed) one request against the tenant/user budget."""
        key = f"api:{tenant_id}:{user_id}" if user_id else f"api:{tenant_id}"
        return self._rate_limiter.check(key, limit=self._rps, window_seconds=1)
