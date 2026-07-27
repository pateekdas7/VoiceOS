"""Real HealthCheck implementations for the Web BFF's /system/health (ADR-005 Sec 9).

Wires the existing, previously-unused-in-production HealthAggregator
(src.libs.health.aggregator) to real infrastructure this BFF already
depends on -- Postgres (the connection it already holds) and Redis, when
configured. No new health-check mechanism is invented; this is exactly the
"UI + thin read on top of existing infrastructure" pattern ADR-005 Sec 9
specifies.
"""

from __future__ import annotations

from typing import Any

from src.libs.health.protocol import HealthStatus


class PostgresHealthCheck:
    """Real liveness probe against the BFF's own Postgres connection."""

    name = "PostgreSQL"

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def check(self) -> HealthStatus:
        try:
            cur = self._conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            return HealthStatus.HEALTHY
        except Exception:
            return HealthStatus.UNHEALTHY


class RedisHealthCheck:
    """Real liveness probe against a Redis client (PING)."""

    name = "Redis"

    def __init__(self, client: Any) -> None:
        self._client = client

    async def check(self) -> HealthStatus:
        try:
            pong = self._client.ping()
            return HealthStatus.HEALTHY if pong else HealthStatus.UNHEALTHY
        except Exception:
            return HealthStatus.UNHEALTHY


__all__ = ["PostgresHealthCheck", "RedisHealthCheck"]
