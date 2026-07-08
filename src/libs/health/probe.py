"""LivenessProbe and ReadinessProbe (V3 Ch12 §12.7, §12.12).

Liveness: is the process alive? A lightweight self-check with no
dependency I/O — failure means "restart me" (V3 Ch12 §12.12).

Readiness: is the service ready to accept traffic? Liveness AND every hard
dependency healthy — failure means "stop routing new work here, but don't
restart" (V3 Ch12 §12.12).

Architecture: V3 Ch12 (Health Monitoring).
"""

from __future__ import annotations

from collections.abc import Sequence

from src.libs.health.protocol import HealthCheck, HealthStatus


class LivenessProbe:
    """Process-alive check. Healthy by default; a watchdog marks it unhealthy on stall."""

    name = "liveness"

    def __init__(self) -> None:
        self._alive = True

    def mark_unhealthy(self) -> None:
        """Record that the process should be considered dead (e.g. watchdog timeout)."""
        self._alive = False

    def mark_healthy(self) -> None:
        """Record recovery (rarely used — liveness failures normally trigger a restart)."""
        self._alive = True

    async def check(self) -> HealthStatus:
        return HealthStatus.HEALTHY if self._alive else HealthStatus.UNHEALTHY


class ReadinessProbe:
    """Ready-for-traffic check: liveness AND all hard dependencies healthy."""

    name = "readiness"

    def __init__(self, liveness: LivenessProbe, dependencies: Sequence[HealthCheck] = ()) -> None:
        """
        Args:
            liveness: The service's LivenessProbe — readiness implies liveness.
            dependencies: Hard dependencies (GPU/Redis/DB/model executors) that
                must all be healthy for the service to be ready.
        """
        self._liveness = liveness
        self._dependencies = list(dependencies)

    async def check(self) -> HealthStatus:
        if await self._liveness.check() != HealthStatus.HEALTHY:
            return HealthStatus.UNHEALTHY

        worst = HealthStatus.HEALTHY
        for dependency in self._dependencies:
            status = await dependency.check()
            if status == HealthStatus.UNHEALTHY:
                return HealthStatus.UNHEALTHY
            if status == HealthStatus.DEGRADED:
                worst = HealthStatus.DEGRADED
        return worst
