"""Integration tests: RedisClient, TTLGuard, DistributedLock, RateLimiter against real Redis.

Requires REDIS_URL. Skipped automatically when absent (see requires_redis).

Run:
    REDIS_URL=redis://localhost:6379/0 \\
    pytest tests/integration/libs/test_redis_integration.py -v

Architecture: V3 Ch4 (Redis Architecture); Sprint-013.
"""

from __future__ import annotations

from src.libs.redis_client.client import RedisClient
from src.libs.redis_client.lock import DistributedLock
from src.libs.redis_client.rate_limiter import RateLimiter
from src.libs.redis_client.ttl_guard import TTLGuard
from tests.fixtures.redis import TestRedis
from tests.integration.conftest import requires_redis

_KEY_PREFIX = "voiceos:test:sprint013:"


@requires_redis
class TestRedisClientIntegration:
    def test_health_check_against_real_redis(self) -> None:
        with TestRedis() as r:
            client = RedisClient(r.client)
            assert client.health_check() is True


@requires_redis
class TestTTLGuardIntegration:
    def test_every_written_key_has_a_ttl(self) -> None:
        with TestRedis() as r:
            r.flush()
            guard = TTLGuard(r.client)
            key = _KEY_PREFIX + "ttl_check"
            guard.set(key, b"data", ex=60)
            ttl = r.client.ttl(key)
            assert ttl > 0


@requires_redis
class TestDistributedLockIntegration:
    def test_redis_lock_acquire_release(self) -> None:
        """acquire + work + release cycle against real Redis (Sprint-013 required test)."""
        with TestRedis() as r:
            r.flush()
            lock = DistributedLock(r.client, ttl_ms=5000)
            token = lock.acquire(_KEY_PREFIX + "call-1", owner_id="worker-1")
            assert token is not None
            assert token.fencing_token >= 1

            # Work happens here in production; simulate a second contender.
            blocked = lock.acquire(_KEY_PREFIX + "call-1", owner_id="worker-2")
            assert blocked is None

            released = lock.release(token)
            assert released is True

            now_available = lock.acquire(_KEY_PREFIX + "call-1", owner_id="worker-2")
            assert now_available is not None
            assert now_available.fencing_token > token.fencing_token

    def test_fencing_rejects_stale_owner_release(self) -> None:
        with TestRedis() as r:
            r.flush()
            lock = DistributedLock(r.client)
            token_a = lock.acquire(_KEY_PREFIX + "call-2", owner_id="worker-A")
            assert token_a is not None
            # Simulate worker-A's session being superseded by force-clearing the key.
            r.client.delete(lock._lock_key(_KEY_PREFIX + "call-2"))
            token_b = lock.acquire(_KEY_PREFIX + "call-2", owner_id="worker-B")
            assert token_b is not None

            assert lock.release(token_a) is False
            assert lock.release(token_b) is True


@requires_redis
class TestRateLimiterIntegration:
    def test_blocks_after_limit_against_real_redis(self) -> None:
        with TestRedis() as r:
            r.flush()
            limiter = RateLimiter(r.client)
            results = [limiter.check(_KEY_PREFIX + "tenant-x", limit=5, window_seconds=5) for _ in range(6)]
            assert [res.allowed for res in results] == [True] * 5 + [False]
