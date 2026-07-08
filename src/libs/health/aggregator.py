"""HealthAggregator — combines component health checks into one verdict, and
the ``/health/live`` / ``/health/ready`` ASGI endpoints (V3 Ch12 §12.6, §12.9).

Architecture: V3 Ch12 (Health Monitoring); Sprint-016 AC (health HTTP endpoints).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from src.libs.health.probe import LivenessProbe, ReadinessProbe
from src.libs.health.protocol import HealthCheck, HealthStatus


@dataclass(frozen=True)
class HealthReport:
    """The aggregated health verdict across all registered components."""

    overall: HealthStatus
    components: dict[str, HealthStatus] = field(default_factory=dict)


class HealthAggregator:
    """Collects per-component :class:`HealthCheck` results into a :class:`HealthReport`.

    Overall status is the worst of its components: any UNHEALTHY component
    makes the aggregate UNHEALTHY; otherwise any DEGRADED component makes it
    DEGRADED; otherwise HEALTHY.
    """

    def __init__(self, checks: Sequence[HealthCheck] = ()) -> None:
        self._checks: list[HealthCheck] = list(checks)

    def register(self, check: HealthCheck) -> None:
        """Add a component health check to the aggregate."""
        self._checks.append(check)

    async def report(self) -> HealthReport:
        """Run every registered check and return the aggregated report."""
        components: dict[str, HealthStatus] = {}
        for check in self._checks:
            components[check.name] = await check.check()
        return HealthReport(overall=_worst_of(components.values()), components=components)


def _worst_of(statuses: Iterable[HealthStatus]) -> HealthStatus:
    statuses = list(statuses)
    if any(status == HealthStatus.UNHEALTHY for status in statuses):
        return HealthStatus.UNHEALTHY
    if any(status == HealthStatus.DEGRADED for status in statuses):
        return HealthStatus.DEGRADED
    return HealthStatus.HEALTHY


def create_health_app(liveness: LivenessProbe, readiness: ReadinessProbe) -> Starlette:
    """Build the ``GET /health/live`` / ``GET /health/ready`` ASGI app for one service.

    Args:
        liveness: The service's LivenessProbe.
        readiness: The service's ReadinessProbe (wraps ``liveness`` plus dependencies).

    Returns:
        A Starlette app exposing both endpoints — 200 when healthy, 503 otherwise.
    """

    async def live(_request: Request) -> JSONResponse:
        status = await liveness.check()
        return JSONResponse({"status": status.value}, status_code=200 if status == HealthStatus.HEALTHY else 503)

    async def ready(_request: Request) -> JSONResponse:
        status = await readiness.check()
        return JSONResponse({"status": status.value}, status_code=200 if status == HealthStatus.HEALTHY else 503)

    return Starlette(routes=[Route("/health/live", live), Route("/health/ready", ready)])
