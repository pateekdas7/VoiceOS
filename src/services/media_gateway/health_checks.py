"""HealthCheck implementations for the CPU media-gateway node (V3 Ch12).

The CPU node is not ready to accept traffic unless every GPU adapter it
depends on (STT, LLM, TTS) is reachable. Each adapter's remote service
exposes a plain ``GET /health`` on its port — HTTP < 400 within the
timeout counts as ``HEALTHY``, any transport error is ``UNHEALTHY``, a
non-2xx response is ``DEGRADED`` (server is up but not serving requests).

Wired into ``ReadinessProbe`` inside ``create_twilio_media_stream_app`` so
``GET /health/ready`` returns 503 whenever an orchestrator (k8s, systemd,
load balancer) should stop routing new calls here.

Architecture: V3 Ch12 (Health Monitoring); V1 Ch26 (Runtime Topology).
"""

from __future__ import annotations

import logging

import httpx

from src.libs.health.protocol import HealthStatus

_log = logging.getLogger("voiceos.media_gateway.health")

_DEFAULT_TIMEOUT_S = 3.0


class GpuServiceHealthCheck:
    """Probe a single remote GPU-hosted service's ``/health`` endpoint.

    Satisfies the ``HealthCheck`` protocol so it can be registered with
    ``HealthAggregator`` / ``ReadinessProbe``.
    """

    def __init__(
        self,
        name: str,
        url: str,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self.name = name
        self._url = url
        self._timeout_s = timeout_s

    async def check(self) -> HealthStatus:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.get(self._url)
        except Exception as exc:
            _log.warning("gpu health check %s unreachable at %s: %s", self.name, self._url, exc)
            return HealthStatus.UNHEALTHY
        if 200 <= resp.status_code < 300:
            return HealthStatus.HEALTHY
        _log.warning("gpu health check %s returned http=%d", self.name, resp.status_code)
        return HealthStatus.DEGRADED


def build_gpu_health_checks(
    gpu_host: str,
    *,
    stt_port: int = 8100,
    llm_port: int = 8000,
    tts_port: int = 8200,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> list[GpuServiceHealthCheck]:
    """Convenience factory: one probe per GPU-hosted model service."""
    return [
        GpuServiceHealthCheck("stt", f"http://{gpu_host}:{stt_port}/health", timeout_s=timeout_s),
        GpuServiceHealthCheck("llm", f"http://{gpu_host}:{llm_port}/health", timeout_s=timeout_s),
        GpuServiceHealthCheck("tts", f"http://{gpu_host}:{tts_port}/health", timeout_s=timeout_s),
    ]


__all__ = ["GpuServiceHealthCheck", "build_gpu_health_checks"]
