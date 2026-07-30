"""HealthVerifier — post-recovery health verification for System X."""
from __future__ import annotations

import logging

import httpx

_log = logging.getLogger("system_x.health_verifier")

_SERVICE_HEALTH_URLS: dict[str, str] = {
    "stt": "http://{gpu_host}:8100/health",
    "llm": "http://{gpu_host}:8000/health",
    "tts": "http://{gpu_host}:8200/health",
}

_TIMEOUT = 5.0


class HealthVerifier:
    def __init__(self, gpu_host: str, prometheus_url: str) -> None:
        self._gpu_host = gpu_host
        self._prometheus_url = prometheus_url

    async def verify(self, affected_services: list[str]) -> dict[str, object]:
        """Probe each affected service and return a health snapshot dict.

        Keys: service name -> {"status": "healthy"|"degraded"|"unreachable", "latency_ms": float|None}
        """
        results: dict[str, object] = {}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for svc in affected_services:
                url_template = _SERVICE_HEALTH_URLS.get(svc)
                if url_template:
                    url = url_template.format(gpu_host=self._gpu_host)
                    try:
                        resp = await client.get(url)
                        ok = resp.status_code < 400
                        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                        results[svc] = {
                            "status": "healthy" if ok else "degraded",
                            "http_status": resp.status_code,
                            "detail": body,
                        }
                    except Exception as exc:
                        results[svc] = {"status": "unreachable", "error": str(exc)}
                else:
                    # For k8s-native services, check Prometheus for recent errors
                    results[svc] = await self._check_via_prometheus(svc)
        return results

    async def all_healthy(self, health_snapshot: dict[str, object]) -> bool:
        """Return True only if every service reports healthy."""
        for svc, state in health_snapshot.items():
            if isinstance(state, dict) and state.get("status") != "healthy":
                return False
        return True

    async def _check_via_prometheus(self, svc: str) -> dict[str, object]:
        svc_key = svc.replace("_", "_")
        query = f'voiceos:{svc_key}_error_rate:ratio_5m'
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{self._prometheus_url}/api/v1/query",
                    params={"query": query},
                )
                data = resp.json()
                result = data.get("data", {}).get("result", [])
                if result:
                    error_rate = float(result[0].get("value", [None, "0"])[1])
                    status = "healthy" if error_rate < 0.05 else "degraded"
                    return {"status": status, "error_rate": error_rate}
        except Exception as exc:
            return {"status": "unknown", "error": str(exc)}
        return {"status": "healthy"}


__all__ = ["HealthVerifier"]
