"""Route-level tests for the Web API liveness/readiness contract."""

from __future__ import annotations

from unittest.mock import MagicMock

from starlette.testclient import TestClient

from src.libs.health.aggregator import HealthAggregator
from src.libs.health.protocol import HealthStatus
from src.services.web_api.api import create_web_api


class _Check:
    name = "dependency"

    def __init__(self, status: HealthStatus) -> None:
        self.status = status

    async def check(self) -> HealthStatus:
        return self.status


def _app(status: HealthStatus) -> object:
    return create_web_api(
        session_codec=MagicMock(),
        google_oauth=MagicMock(),
        platform_admin=MagicMock(),
        user_service=MagicMock(),
        frontend_base_url="http://frontend",
        bff_public_url="http://webapi",
        health_aggregator=HealthAggregator([_Check(status)]),
    )


def test_health_live_does_not_depend_on_dependencies() -> None:
    client = TestClient(_app(HealthStatus.UNHEALTHY))
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_health_ready_returns_200_when_dependencies_are_healthy() -> None:
    client = TestClient(_app(HealthStatus.HEALTHY))
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_health_ready_returns_503_when_dependency_is_unhealthy() -> None:
    client = TestClient(_app(HealthStatus.UNHEALTHY))
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy"}
