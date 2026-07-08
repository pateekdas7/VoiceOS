"""Unit tests for HealthCheck/LivenessProbe/ReadinessProbe/HealthAggregator and
the /health/live, /health/ready ASGI endpoints (V3 Ch12)."""

from __future__ import annotations

from starlette.testclient import TestClient

from src.libs.health.aggregator import HealthAggregator, create_health_app
from src.libs.health.probe import LivenessProbe, ReadinessProbe
from src.libs.health.protocol import HealthStatus


class _FakeDependencyCheck:
    name = "postgres"

    def __init__(self, status: HealthStatus) -> None:
        self._status = status

    async def check(self) -> HealthStatus:
        return self._status


class TestLivenessProbe:
    async def test_healthy_by_default(self) -> None:
        probe = LivenessProbe()
        assert await probe.check() == HealthStatus.HEALTHY

    async def test_mark_unhealthy(self) -> None:
        probe = LivenessProbe()
        probe.mark_unhealthy()
        assert await probe.check() == HealthStatus.UNHEALTHY

    async def test_mark_healthy_recovers(self) -> None:
        probe = LivenessProbe()
        probe.mark_unhealthy()
        probe.mark_healthy()
        assert await probe.check() == HealthStatus.HEALTHY


class TestReadinessProbe:
    async def test_ready_with_no_dependencies(self) -> None:
        liveness = LivenessProbe()
        readiness = ReadinessProbe(liveness)
        assert await readiness.check() == HealthStatus.HEALTHY

    async def test_unready_when_not_alive(self) -> None:
        liveness = LivenessProbe()
        liveness.mark_unhealthy()
        readiness = ReadinessProbe(liveness)
        assert await readiness.check() == HealthStatus.UNHEALTHY

    async def test_unready_when_hard_dependency_unhealthy(self) -> None:
        liveness = LivenessProbe()
        dep = _FakeDependencyCheck(HealthStatus.UNHEALTHY)
        readiness = ReadinessProbe(liveness, [dep])
        assert await readiness.check() == HealthStatus.UNHEALTHY

    async def test_degraded_when_dependency_degraded(self) -> None:
        liveness = LivenessProbe()
        dep = _FakeDependencyCheck(HealthStatus.DEGRADED)
        readiness = ReadinessProbe(liveness, [dep])
        assert await readiness.check() == HealthStatus.DEGRADED


class TestHealthAggregator:
    async def test_report_aggregates_all_components(self) -> None:
        aggregator = HealthAggregator(
            [_FakeDependencyCheck(HealthStatus.HEALTHY)],
        )
        report = await aggregator.report()
        assert report.overall == HealthStatus.HEALTHY
        assert report.components["postgres"] == HealthStatus.HEALTHY

    async def test_register_adds_a_check(self) -> None:
        aggregator = HealthAggregator()
        aggregator.register(_FakeDependencyCheck(HealthStatus.DEGRADED))
        report = await aggregator.report()
        assert report.overall == HealthStatus.DEGRADED

    async def test_overall_is_worst_of_components(self) -> None:
        class HealthyOther:
            name = "redis"

            async def check(self) -> HealthStatus:
                return HealthStatus.HEALTHY

        aggregator = HealthAggregator(
            [_FakeDependencyCheck(HealthStatus.UNHEALTHY), HealthyOther()],
        )
        report = await aggregator.report()
        assert report.overall == HealthStatus.UNHEALTHY


class TestHealthEndpoints:
    def test_live_returns_200_when_healthy(self) -> None:
        liveness = LivenessProbe()
        readiness = ReadinessProbe(liveness)
        app = create_health_app(liveness, readiness)
        client = TestClient(app)

        response = client.get("/health/live")

        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

    def test_live_returns_503_when_unhealthy(self) -> None:
        liveness = LivenessProbe()
        liveness.mark_unhealthy()
        readiness = ReadinessProbe(liveness)
        app = create_health_app(liveness, readiness)
        client = TestClient(app)

        response = client.get("/health/live")

        assert response.status_code == 503

    def test_ready_returns_200_only_when_all_dependencies_healthy(self) -> None:
        liveness = LivenessProbe()
        readiness = ReadinessProbe(liveness, [_FakeDependencyCheck(HealthStatus.HEALTHY)])
        app = create_health_app(liveness, readiness)
        client = TestClient(app)

        response = client.get("/health/ready")

        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

    def test_ready_returns_503_when_a_dependency_is_unhealthy(self) -> None:
        liveness = LivenessProbe()
        readiness = ReadinessProbe(liveness, [_FakeDependencyCheck(HealthStatus.UNHEALTHY)])
        app = create_health_app(liveness, readiness)
        client = TestClient(app)

        response = client.get("/health/ready")

        assert response.status_code == 503
