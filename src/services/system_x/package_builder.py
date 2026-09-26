"""IncidentPackageBuilder — assembles a redacted context bundle for Claude analysis."""
from __future__ import annotations

from datetime import UTC, datetime

from .models import IngestAlert, IncidentSeverity

# Never include these keys in the package
_REDACTED_KEYS = frozenset(
    {"password", "secret", "token", "key", "dsn", "credential", "auth", "api_key"}
)


def _redact(d: dict) -> dict:
    """Recursively redact sensitive keys from a dict."""
    out: dict = {}
    for k, v in d.items():
        if any(s in k.lower() for s in _REDACTED_KEYS):
            out[k] = "[REDACTED]"
        elif isinstance(v, dict):
            out[k] = _redact(v)
        else:
            out[k] = v
    return out


class IncidentPackageBuilder:
    def __init__(self, prometheus_url: str) -> None:
        self._prometheus_url = prometheus_url

    def build(
        self,
        incident_id: str,
        severity: IncidentSeverity,
        alerts: list[IngestAlert],
        affected_services: list[str],
    ) -> dict:
        """Build a JSON-serializable package for Claude.

        Returns a plain dict — never contains secrets.
        """
        now = datetime.now(UTC)
        package = {
            "incident_id": incident_id,
            "severity": str(severity),
            "detected_at": now.isoformat(),
            "affected_services": affected_services,
            "alerts": [
                {
                    "fingerprint": a.fingerprint,
                    "alert_name": a.alert_name,
                    "severity": a.severity,
                    "service": a.labels.get("service", "unknown"),
                    "labels": _redact(a.labels),
                    "annotations": _redact(a.annotations),
                    "fired_at": a.fired_at.isoformat(),
                }
                for a in alerts
            ],
            "metrics_snapshot": self._fetch_metrics_snapshot(affected_services),
        }
        return package

    def _fetch_metrics_snapshot(self, services: list[str]) -> dict:
        """Fetch current Prometheus metrics for affected services.

        Non-critical — returns partial or empty results on failure rather than
        raising, so a Prometheus outage never blocks incident analysis.
        """
        import httpx

        snapshot: dict[str, object] = {}
        queries: dict[str, str] = {
            "stt": "voiceos:stt_latency_ms:p95_5m",
            "llm": "voiceos:llm_ttft_ms:p95_5m",
            "tts": "voiceos:tts_first_clause_latency_ms:p95_5m",
            "conversation_engine": "voiceos:conversation_engine_latency_ms:p95_5m",
        }
        for svc in services:
            q = queries.get(svc)
            if not q:
                continue
            try:
                resp = httpx.get(
                    f"{self._prometheus_url}/api/v1/query",
                    params={"query": q},
                    timeout=3.0,
                )
                data = resp.json()
                result = data.get("data", {}).get("result", [])
                if result:
                    snapshot[svc] = result[0].get("value", [None, "N/A"])[1]
            except Exception:
                snapshot[svc] = "unavailable"
        return snapshot


__all__ = ["IncidentPackageBuilder"]
