"""Unit tests for real HealthCheck implementations (ADR-005 Sec 9)."""

from __future__ import annotations

from src.libs.health.protocol import HealthStatus
from src.services.web_api.health_checks import PostgresHealthCheck, RedisHealthCheck


class _FakeCursor:
    def __init__(self, *, raises: bool = False) -> None:
        self._raises = raises

    def execute(self, sql: str) -> None:
        if self._raises:
            raise RuntimeError("connection lost")

    def fetchone(self) -> tuple[int]:
        return (1,)


class _FakeConn:
    def __init__(self, *, raises: bool = False) -> None:
        self._raises = raises

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(raises=self._raises)


class _FakeRedis:
    def __init__(self, *, pong: bool = True, raises: bool = False) -> None:
        self._pong = pong
        self._raises = raises

    def ping(self) -> bool:
        if self._raises:
            raise RuntimeError("connection refused")
        return self._pong


class TestPostgresHealthCheck:
    async def test_healthy_when_query_succeeds(self) -> None:
        check = PostgresHealthCheck(_FakeConn())
        assert await check.check() == HealthStatus.HEALTHY

    async def test_unhealthy_when_query_raises(self) -> None:
        check = PostgresHealthCheck(_FakeConn(raises=True))
        assert await check.check() == HealthStatus.UNHEALTHY

    def test_name_is_postgresql(self) -> None:
        assert PostgresHealthCheck(_FakeConn()).name == "PostgreSQL"


class TestRedisHealthCheck:
    async def test_healthy_when_ping_succeeds(self) -> None:
        check = RedisHealthCheck(_FakeRedis(pong=True))
        assert await check.check() == HealthStatus.HEALTHY

    async def test_unhealthy_when_ping_returns_false(self) -> None:
        check = RedisHealthCheck(_FakeRedis(pong=False))
        assert await check.check() == HealthStatus.UNHEALTHY

    async def test_unhealthy_when_ping_raises(self) -> None:
        check = RedisHealthCheck(_FakeRedis(raises=True))
        assert await check.check() == HealthStatus.UNHEALTHY

    def test_name_is_redis(self) -> None:
        assert RedisHealthCheck(_FakeRedis()).name == "Redis"
