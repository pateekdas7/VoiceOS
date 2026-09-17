"""Unit tests for CPU-node GPU service health checks (V3 Ch12).

Uses respx to stub the HTTP layer so the tests never make real network
calls but still exercise the real httpx.AsyncClient path.
"""

from __future__ import annotations

import httpx
import pytest

from src.libs.health.probe import LivenessProbe, ReadinessProbe
from src.libs.health.protocol import HealthStatus
from src.services.media_gateway.health_checks import (
    GpuServiceHealthCheck,
    build_gpu_health_checks,
)


class _StubTransport(httpx.AsyncBaseTransport):
    def __init__(self, status_code: int | None = None, *, raise_exc: Exception | None = None) -> None:
        self._status_code = status_code
        self._raise_exc = raise_exc

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._raise_exc is not None:
            raise self._raise_exc
        assert self._status_code is not None
        return httpx.Response(self._status_code)


@pytest.fixture(autouse=True)
def _patch_httpx(monkeypatch: pytest.MonkeyPatch) -> dict[str, _StubTransport]:
    """Route every AsyncClient() call through a per-test transport dict."""
    active: dict[str, _StubTransport] = {}
    real_init = httpx.AsyncClient.__init__

    def _init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        # active["transport"] is set by individual tests
        kwargs["transport"] = active["transport"]
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)
    return active


class TestGpuServiceHealthCheck:
    @pytest.mark.asyncio
    async def test_2xx_response_is_healthy(self, _patch_httpx: dict[str, _StubTransport]) -> None:
        _patch_httpx["transport"] = _StubTransport(status_code=200)
        check = GpuServiceHealthCheck("stt", "http://gpu:8100/health")

        assert await check.check() == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_500_response_is_degraded(self, _patch_httpx: dict[str, _StubTransport]) -> None:
        _patch_httpx["transport"] = _StubTransport(status_code=500)
        check = GpuServiceHealthCheck("stt", "http://gpu:8100/health")

        assert await check.check() == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_connection_error_is_unhealthy(
        self, _patch_httpx: dict[str, _StubTransport]
    ) -> None:
        _patch_httpx["transport"] = _StubTransport(raise_exc=httpx.ConnectError("refused"))
        check = GpuServiceHealthCheck("stt", "http://gpu:8100/health")

        assert await check.check() == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_timeout_is_unhealthy(self, _patch_httpx: dict[str, _StubTransport]) -> None:
        _patch_httpx["transport"] = _StubTransport(
            raise_exc=httpx.ReadTimeout("timed out")
        )
        check = GpuServiceHealthCheck("stt", "http://gpu:8100/health")

        assert await check.check() == HealthStatus.UNHEALTHY

    def test_build_gpu_health_checks_default_ports(self) -> None:
        checks = build_gpu_health_checks("gpu.example.com")

        names = [c.name for c in checks]
        assert names == ["stt", "llm", "tts"]


class TestReadinessProbeWithGpuChecks:
    @pytest.mark.asyncio
    async def test_all_gpu_healthy_ready(self, _patch_httpx: dict[str, _StubTransport]) -> None:
        _patch_httpx["transport"] = _StubTransport(status_code=200)
        probe = ReadinessProbe(LivenessProbe(), build_gpu_health_checks("gpu"))

        assert await probe.check() == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_one_gpu_unreachable_not_ready(
        self, _patch_httpx: dict[str, _StubTransport]
    ) -> None:
        _patch_httpx["transport"] = _StubTransport(raise_exc=httpx.ConnectError("no route"))
        probe = ReadinessProbe(LivenessProbe(), build_gpu_health_checks("gpu"))

        assert await probe.check() == HealthStatus.UNHEALTHY
