"""Concrete HTTP query adapters for the existing observability stack (ADR-006 Sec 3.2.1).

Implements the read-only ports ``evidence_bundler.py``/``capacity_planner.py``
define (``MetricsQueryPort``, ``LogQueryPort``, ``TraceQueryPort``,
``MetricsHistoryPort``) against Prometheus/Thanos, Loki, and Jaeger's own
HTTP *query* APIs -- never their emission/write paths. Default base URLs
match the real in-cluster service DNS names already provisioned as Grafana
datasources (``monitoring/grafana/provisioning/``), so these adapters point
at the SAME already-deployed stack, not a new one.

Deliberately synchronous (``httpx.Client``, not ``AsyncClient``): every
caller of these ports runs inside a scheduled batch analysis pass (a K8s
CronJob), not a live request-serving path -- a blocking HTTP call here does
not compete with real-time voice-pipeline latency budgets the way it would
in ``llm_runtime``/``stt``/``tts``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from src.services.ops_intelligence.reasoning.evidence_bundler import MetricSample

DEFAULT_PROMETHEUS_BASE_URL = "http://prometheus.voiceos-ops.svc.cluster.local:9090"
DEFAULT_LOKI_BASE_URL = "http://loki.voiceos-ops.svc.cluster.local:3100"
DEFAULT_JAEGER_BASE_URL = "http://jaeger-query.voiceos-ops.svc.cluster.local:16686"

DEFAULT_LOG_LOOKBACK = timedelta(minutes=15)
DEFAULT_TRACE_LOOKBACK = timedelta(minutes=15)


class PrometheusQueryAdapter:
    """``MetricsQueryPort``/``MetricsHistoryPort`` backed by Prometheus's ``/api/v1/query(_range)``."""

    def __init__(self, base_url: str = DEFAULT_PROMETHEUS_BASE_URL, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._resource_queries: dict[str, str] = {}
        self._capacity_queries: dict[str, str] = {}

    def instant(self, query: str) -> MetricSample | None:
        """Implements ``evidence_bundler.MetricsQueryPort.instant()``."""
        import httpx

        with httpx.Client(timeout=self._timeout) as client:
            response = client.get(f"{self._base_url}/api/v1/query", params={"query": query})
            response.raise_for_status()
            body = response.json()

        result = body.get("data", {}).get("result", [])
        if not result:
            return None
        timestamp_raw, value_raw = result[0]["value"]
        return MetricSample(value=float(value_raw), observed_at=datetime.fromtimestamp(float(timestamp_raw), tz=UTC))

    def historical_series(self, resource: str, *, days: int) -> tuple[tuple[datetime, float], ...]:
        """Implements ``capacity_planner.MetricsHistoryPort.historical_series()``.

        ``resource`` names a recording-rule/metric series representing that
        resource's usage (e.g. ``voiceos_gpu_fleet_vram_used_mb`` for
        ``resource="gpu"``) -- the exact query per resource is supplied by
        the caller via :meth:`with_resource_queries`, defaulting to a plain
        metric-name lookup when not configured.
        """
        query = self._resource_queries.get(resource, resource)
        import httpx

        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        with httpx.Client(timeout=self._timeout) as client:
            response = client.get(
                f"{self._base_url}/api/v1/query_range",
                params={"query": query, "start": start.timestamp(), "end": end.timestamp(), "step": "1d"},
            )
            response.raise_for_status()
            body = response.json()

        result = body.get("data", {}).get("result", [])
        if not result:
            return ()
        values = result[0].get("values", [])
        return tuple((datetime.fromtimestamp(float(ts), tz=UTC), float(val)) for ts, val in values)

    def current_capacity(self, resource: str) -> float:
        query = self._capacity_queries.get(resource, f"{resource}_capacity")
        sample = self.instant(query)
        return sample.value if sample is not None else 0.0

    def with_resource_queries(
        self, resource_queries: dict[str, str], capacity_queries: dict[str, str]
    ) -> PrometheusQueryAdapter:
        """Configure the PromQL query used per ``resource`` name (fluent setter, returns self)."""
        self._resource_queries = resource_queries
        self._capacity_queries = capacity_queries
        return self


class LokiQueryAdapter:
    """``LogQueryPort`` backed by Loki's ``/loki/api/v1/query_range``."""

    def __init__(self, base_url: str = DEFAULT_LOKI_BASE_URL, *, timeout: float = 10.0, lookback: timedelta = DEFAULT_LOG_LOOKBACK) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._lookback = lookback

    def query(self, logql: str, *, limit: int = 20) -> tuple[str, ...]:
        import httpx

        end = datetime.now(UTC)
        start = end - self._lookback
        with httpx.Client(timeout=self._timeout) as client:
            response = client.get(
                f"{self._base_url}/loki/api/v1/query_range",
                params={
                    "query": logql,
                    "limit": limit,
                    "start": int(start.timestamp() * 1e9),
                    "end": int(end.timestamp() * 1e9),
                },
            )
            response.raise_for_status()
            body = response.json()

        lines: list[str] = []
        for stream in body.get("data", {}).get("result", []):
            for _timestamp_ns, log_line in stream.get("values", []):
                lines.append(log_line)
        return tuple(lines[:limit])


class JaegerQueryAdapter:
    """``TraceQueryPort`` backed by Jaeger's ``/api/traces`` query endpoint.

    Expects ``trace_query`` in the ``"service=<name>"`` shape
    ``evidence_bundler._correlate_traces`` already produces.
    """

    def __init__(self, base_url: str = DEFAULT_JAEGER_BASE_URL, *, timeout: float = 10.0, lookback: timedelta = DEFAULT_TRACE_LOOKBACK) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._lookback = lookback

    def query(self, trace_query: str, *, limit: int = 5) -> tuple[str, ...]:
        service = trace_query.split("=", 1)[1] if "=" in trace_query else trace_query
        import httpx

        end = datetime.now(UTC)
        start = end - self._lookback
        with httpx.Client(timeout=self._timeout) as client:
            response = client.get(
                f"{self._base_url}/api/traces",
                params={
                    "service": service,
                    "limit": limit,
                    "start": int(start.timestamp() * 1e6),
                    "end": int(end.timestamp() * 1e6),
                    "tags": '{"error":"true"}',
                },
            )
            response.raise_for_status()
            body = response.json()

        return tuple(str(trace.get("traceID", "")) for trace in body.get("data", []))[:limit]


class EventBusQueryAdapter:
    """``EventBusQueryPort`` backed by a replay of the existing ``voiceos-events`` stream.

    Reuses ``EventBus.replay_from()`` (V3 Ch3) rather than a new read path --
    filters client-side by ``event_type``/``tenant_id`` since Redis Streams
    has no server-side event_type index.
    """

    def __init__(self, event_bus: Any) -> None:
        self._event_bus = event_bus

    def recent(self, event_type: str, *, tenant_id: str | None, limit: int = 20) -> tuple[dict[str, object], ...]:
        entries = self._event_bus.replay_from()
        matched: list[dict[str, object]] = []
        for _entry_id, envelope in reversed(entries):
            if envelope.event_type != event_type:
                continue
            if tenant_id is not None and str(envelope.tenant_id) != tenant_id:
                continue
            matched.append(envelope.payload)
            if len(matched) >= limit:
                break
        return tuple(matched)


__all__ = [
    "DEFAULT_JAEGER_BASE_URL",
    "DEFAULT_LOKI_BASE_URL",
    "DEFAULT_PROMETHEUS_BASE_URL",
    "EventBusQueryAdapter",
    "JaegerQueryAdapter",
    "LokiQueryAdapter",
    "PrometheusQueryAdapter",
]
