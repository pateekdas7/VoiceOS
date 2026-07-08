"""RedisHealthCheck — HealthCheck protocol adapter over RedisClient (Sprint-016).

Lets a ``RedisClient`` be registered directly with a
:class:`~src.libs.health.aggregator.HealthAggregator` / used as a
:class:`~src.libs.health.probe.ReadinessProbe` hard dependency, closing the
loop between the health-monitoring layer (V3 Ch12) and the existing Redis
reliability layer (V3 Ch4).

Architecture: V3 Ch12 (Health Monitoring) §12.6; V3 Ch4 (Redis Architecture).
"""

from __future__ import annotations

from src.libs.health.protocol import HealthStatus
from src.libs.redis_client.client import RedisClient


class RedisHealthCheck:
    """Adapts :meth:`RedisClient.health_check` to the ``HealthCheck`` protocol."""

    name = "redis"

    def __init__(self, client: RedisClient) -> None:
        self._client = client

    async def check(self) -> HealthStatus:
        """Ping Redis via the wrapped client. Never raises."""
        return HealthStatus.HEALTHY if self._client.health_check() else HealthStatus.UNHEALTHY
