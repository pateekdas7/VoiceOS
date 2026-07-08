"""Unit tests: RedisClient and TTLGuard.

Architecture: V3 Ch4 (Redis Architecture); Sprint-013.
"""

from __future__ import annotations

import pytest

from src.libs.circuit_breaker.breaker import CircuitBreaker, CircuitBreakerConfig, CircuitOpenError, CircuitState
from src.libs.health.aggregator import HealthAggregator
from src.libs.health.probe import LivenessProbe, ReadinessProbe
from src.libs.health.protocol import HealthStatus
from src.libs.redis_client.client import RedisClient
from src.libs.redis_client.health_check import RedisHealthCheck
from src.libs.redis_client.ttl_guard import MissingTTLError, TTLGuard
from tests.fixtures.redis import FakeRedisClient


class TestTTLGuard:
    def test_ttl_guard_enforces_ttl(self, fake_redis: FakeRedisClient) -> None:
        """set() without ex/px/exat raises MissingTTLError (RI-3 / V3 Ch4)."""
        guard = TTLGuard(fake_redis)
        with pytest.raises(MissingTTLError):
            guard.set("voiceos:tenant-1:session:abc", b"data")

    def test_ttl_guard_allows_ex(self, fake_redis: FakeRedisClient) -> None:
        guard = TTLGuard(fake_redis)
        result = guard.set("voiceos:tenant-1:session:abc", b"data", ex=120)
        assert result is True
        assert fake_redis.get("voiceos:tenant-1:session:abc") == b"data"
        assert fake_redis.ttl("voiceos:tenant-1:session:abc") == 120

    def test_ttl_guard_allows_px(self, fake_redis: FakeRedisClient) -> None:
        guard = TTLGuard(fake_redis)
        result = guard.set("k", b"v", px=5000)
        assert result is True

    def test_ttl_guard_allows_exat(self, fake_redis: FakeRedisClient) -> None:
        guard = TTLGuard(fake_redis)
        result = guard.set("k", b"v", exat=9999999999)
        assert result is True

    def test_ttl_guard_nx_respects_existing_key(self, fake_redis: FakeRedisClient) -> None:
        guard = TTLGuard(fake_redis)
        first = guard.set("k", b"v1", ex=60, nx=True)
        second = guard.set("k", b"v2", ex=60, nx=True)
        assert first is True
        assert second is None
        assert fake_redis.get("k") == b"v1"

    def test_missing_ttl_error_message_includes_key(self, fake_redis: FakeRedisClient) -> None:
        guard = TTLGuard(fake_redis)
        with pytest.raises(MissingTTLError) as exc_info:
            guard.set("voiceos:tenant-1:orphan-key", b"data")
        assert "voiceos:tenant-1:orphan-key" in str(exc_info.value)


class TestRedisClient:
    def test_health_check_true_when_ping_succeeds(self, fake_redis: FakeRedisClient) -> None:
        client = RedisClient(fake_redis)
        assert client.health_check() is True

    def test_health_check_false_on_exception(self) -> None:
        class BrokenRedis:
            def ping(self) -> bool:
                raise ConnectionError("refused")

        client = RedisClient(BrokenRedis())
        assert client.health_check() is False

    def test_raw_exposes_underlying_client(self, fake_redis: FakeRedisClient) -> None:
        client = RedisClient(fake_redis)
        assert client.raw is fake_redis

    def test_close_is_safe_when_unsupported(self, fake_redis: FakeRedisClient) -> None:
        client = RedisClient(fake_redis)
        client.close()  # FakeRedisClient has no close(); must not raise

    def test_close_calls_underlying_close_when_present(self) -> None:
        closed = []

        class WithClose:
            def close(self) -> None:
                closed.append(True)

        RedisClient(WithClose()).close()
        assert closed == [True]

    def test_from_url_builds_pooled_client_without_connecting(self) -> None:
        """from_url() only builds the connection pool — no I/O happens until first command."""
        client = RedisClient.from_url("redis://localhost:6379/0")
        assert client.raw is not None


class TestRedisClientCircuitBreaker:
    """Sprint-016: RedisClient's optional breaker guards Redis operations (V3 Ch14 §14.2)."""

    def test_call_guarded_passes_through_without_breaker(self, fake_redis: FakeRedisClient) -> None:
        client = RedisClient(fake_redis)
        assert client.health_check() is True

    def test_health_check_false_when_breaker_opens(self) -> None:
        class BrokenRedis:
            def ping(self) -> bool:
                raise ConnectionError("refused")

        breaker = CircuitBreaker("redis", CircuitBreakerConfig(failure_threshold=1))
        client = RedisClient(BrokenRedis(), breaker=breaker)

        assert client.health_check() is False  # first failure trips the breaker
        assert breaker.state == CircuitState.OPEN
        assert client.health_check() is False  # second call: breaker OPEN, still False (never raises)

    def test_call_guarded_raises_circuit_open_error_when_open(self, fake_redis: FakeRedisClient) -> None:
        breaker = CircuitBreaker("redis", CircuitBreakerConfig(failure_threshold=1))
        client = RedisClient(fake_redis, breaker=breaker)

        def always_fails() -> None:
            raise ConnectionError("down")

        with pytest.raises(ConnectionError):
            client.call_guarded(always_fails)

        with pytest.raises(CircuitOpenError):
            client.call_guarded(always_fails)


class TestRedisHealthCheck:
    """Sprint-016: RedisHealthCheck adapts RedisClient to the HealthCheck protocol."""

    async def test_healthy_when_ping_succeeds(self, fake_redis: FakeRedisClient) -> None:
        check = RedisHealthCheck(RedisClient(fake_redis))
        assert await check.check() == HealthStatus.HEALTHY

    async def test_unhealthy_when_ping_fails(self) -> None:
        class BrokenRedis:
            def ping(self) -> bool:
                raise ConnectionError("refused")

        check = RedisHealthCheck(RedisClient(BrokenRedis()))
        assert await check.check() == HealthStatus.UNHEALTHY

    async def test_integrates_with_health_aggregator_and_readiness_probe(self, fake_redis: FakeRedisClient) -> None:
        check = RedisHealthCheck(RedisClient(fake_redis))
        aggregator = HealthAggregator([check])
        report = await aggregator.report()
        assert report.overall == HealthStatus.HEALTHY

        readiness = ReadinessProbe(LivenessProbe(), [check])
        assert await readiness.check() == HealthStatus.HEALTHY
