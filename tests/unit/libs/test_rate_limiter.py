"""Unit tests: RateLimiter (sliding window, V3 Ch4 §4.12).

Architecture: V3 Ch4; Sprint-013.
"""

from __future__ import annotations

from src.libs.redis_client.rate_limiter import RateLimiter, RateLimitResult
from tests.fixtures.redis import FakeRedisClient


class TestRateLimiter:
    def test_rate_limiter_blocks_after_limit(self, fake_redis: FakeRedisClient) -> None:
        """11 requests in a 10s window with limit=10 -> the 11th is blocked (Sprint-013 AC)."""
        limiter = RateLimiter(fake_redis)
        results: list[RateLimitResult] = [limiter.check("tenant-1", limit=10, window_seconds=10) for _ in range(11)]

        allowed = [r.allowed for r in results]
        assert allowed == [True] * 10 + [False]
        assert results[-1].retry_after_ms >= 0

    def test_allows_requests_under_limit(self, fake_redis: FakeRedisClient) -> None:
        limiter = RateLimiter(fake_redis)
        result = limiter.check("tenant-1", limit=5, window_seconds=10)
        assert result.allowed is True
        assert result.remaining == 4

    def test_remaining_decreases_with_each_request(self, fake_redis: FakeRedisClient) -> None:
        limiter = RateLimiter(fake_redis)
        first = limiter.check("tenant-1", limit=3, window_seconds=10)
        second = limiter.check("tenant-1", limit=3, window_seconds=10)
        assert first.remaining == 2
        assert second.remaining == 1

    def test_separate_keys_have_independent_budgets(self, fake_redis: FakeRedisClient) -> None:
        limiter = RateLimiter(fake_redis)
        for _ in range(3):
            limiter.check("tenant-1", limit=3, window_seconds=10)
        tenant_2_result = limiter.check("tenant-2", limit=3, window_seconds=10)
        assert tenant_2_result.allowed is True

    def test_retry_after_ms_is_zero_for_key_with_no_entries(self, fake_redis: FakeRedisClient) -> None:
        """Defensive branch: an empty window has no oldest entry to measure from."""
        limiter = RateLimiter(fake_redis)
        assert limiter._retry_after_ms("voiceos:ratelimit:empty-key", now=1000.0, window_seconds=10) == 0

    def test_unblocks_after_window_expires(self, fake_redis: FakeRedisClient) -> None:
        limiter = RateLimiter(fake_redis)
        # Exhaust the budget using a request "in the past" relative to the window.
        past_key = "tenant-3"
        zkey = f"voiceos:ratelimit:{past_key}"
        # Manually seed one stale entry far outside the window.
        fake_redis.zadd(zkey, {"stale-member": 0.0})
        result = limiter.check(past_key, limit=1, window_seconds=1)
        # The stale entry (score=0.0, i.e. epoch) is outside any realistic
        # 1s window, so it is pruned and this request is allowed.
        assert result.allowed is True
