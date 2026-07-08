"""HealthCheck protocol and HealthStatus — the health-monitoring vocabulary (V3 Ch12).

Architecture: V3 Ch12 (Health Monitoring) §12.6, §12.7.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable


class HealthStatus(StrEnum):
    """A component's health verdict (V3 Ch12 §12.6, simplified to the three
    states the sprint's health checks act on: liveness/readiness are boolean
    outcomes derived from this richer per-component signal)."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@runtime_checkable
class HealthCheck(Protocol):
    """A named, async health probe for one dependency or component."""

    name: str

    async def check(self) -> HealthStatus:
        """Return the current health of this component. Must not raise."""
        ...
