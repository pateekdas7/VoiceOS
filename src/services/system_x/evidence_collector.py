"""EvidenceCollector — System X's secure interface to production telemetry.

Claude never queries production systems directly. Every data request from
Claude during a diagnostic session flows through this collector, which:
  1. Validates the query is safe to run
  2. Fetches the data from the appropriate subsystem
  3. Redacts secrets before returning results
  4. Returns structured responses (never raw dumps)

This is the only code path allowed to touch Prometheus, Loki, Redis, or
service health endpoints during an investigation.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

_log = logging.getLogger("system_x.evidence_collector")

# Keys whose values must never appear in evidence sent to Claude
_SECRET_KEYS = frozenset({
    "password", "passwd", "secret", "token", "key", "api_key", "apikey",
    "auth", "credential", "credentials", "access_key", "private_key",
    "client_secret", "app_password", "dsn",
})

# Patterns that indicate a raw credential value (e.g. jwt, bearer, postgres://...)
_SECRET_VALUE_PATTERNS = [
    re.compile(r"(?i)postgresql://[^@]+@"),
    re.compile(r"(?i)redis://:[^@]+@"),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"(?i)sk-[A-Za-z0-9]{20,}"),  # openai-style keys
]

# Prometheus queries allowed per service (whitelist prevents arbitrary PromQL injection)
_ALLOWED_METRIC_QUERIES: dict[str, str] = {
    "stt_latency": "voiceos:stt_latency_ms:p95_5m",
    "stt_errors": "voiceos:stt_error_rate:ratio_5m",
    "llm_ttft": "voiceos:llm_ttft_ms:p95_5m",
    "llm_errors": "voiceos:llm_error_rate:ratio_5m",
    "tts_latency": "voiceos:tts_first_clause_latency_ms:p95_5m",
    "tts_errors": "voiceos:tts_error_rate:ratio_5m",
    "conv_latency": "voiceos:conversation_engine_latency_ms:p95_5m",
    "conv_errors": "voiceos:conversation_engine_error_rate:ratio_5m",
    "node_cpu": "100 - (avg(rate(node_cpu_seconds_total{mode='idle'}[5m])) * 100)",
    "node_memory": "(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100",
    "node_disk": "100 - ((node_filesystem_avail_bytes / node_filesystem_size_bytes) * 100)",
}

_LOKI_BASE_URL = "http://loki.voiceos-ops.svc.cluster.local:3100"
_GPU_HOST = "185.216.21.242"
_AI_PORTS = {"stt": 8100, "llm": 8000, "tts": 8200}
_TIMEOUT = 5.0


def _redact_value(v: Any, key: str = "") -> Any:
    """Recursively redact sensitive values."""
    if isinstance(v, dict):
        return {k: _redact_value(val, k) for k, val in v.items()}
    if isinstance(v, list):
        return [_redact_value(item) for item in v]
    if isinstance(v, str):
        if any(s in key.lower() for s in _SECRET_KEYS):
            return "[REDACTED]"
        for pattern in _SECRET_VALUE_PATTERNS:
            if pattern.search(v):
                return "[REDACTED]"
    return v


def _redact(data: Any) -> Any:
    return _redact_value(data)


class EvidenceCollector:
    """Fetches telemetry for Claude diagnostic sessions.

    All outputs are secrets-redacted before being returned.
    """

    def __init__(self, prometheus_url: str, incident_repo: Any | None = None) -> None:
        self._prometheus_url = prometheus_url
        self._incident_repo = incident_repo  # for query_incident_history

    async def execute(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a tool call from Claude to the appropriate data source."""
        try:
            handler = self._handlers().get(tool_name)
            if handler is None:
                return {"error": f"unknown tool: {tool_name}"}
            result = await handler(tool_input)
            return _redact(result)
        except Exception as exc:
            _log.warning("evidence tool=%s failed: %s", tool_name, exc)
            return {"error": str(exc), "tool": tool_name}

    def _handlers(self) -> dict[str, Any]:
        return {
            "query_metrics": self._query_metrics,
            "query_logs": self._query_logs,
            "check_service_health": self._check_service_health,
            "query_incident_history": self._query_incident_history,
            "query_redis_info": self._query_redis_info,
        }

    async def _query_metrics(self, inp: dict) -> dict:
        metric_key = inp.get("metric_key", "")
        query = _ALLOWED_METRIC_QUERIES.get(metric_key)
        if not query:
            available = list(_ALLOWED_METRIC_QUERIES.keys())
            return {"error": f"metric_key '{metric_key}' not in whitelist", "available_keys": available}

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{self._prometheus_url}/api/v1/query",
                    params={"query": query},
                )
                data = resp.json()
                result = data.get("data", {}).get("result", [])
                values = [{"metric": r.get("metric", {}), "value": r.get("value", [])} for r in result]
                return {"metric_key": metric_key, "query": query, "results": values}
        except Exception as exc:
            return {"error": str(exc)}

    async def _query_logs(self, inp: dict) -> dict:
        service = inp.get("service", "")
        last_n = min(int(inp.get("last_n_lines", 50)), 200)  # cap at 200
        level = inp.get("level", "error")

        loki_query = f'{{app="{service}"}} |= "{level}"'
        try:
            import time
            end_ns = int(time.time() * 1e9)
            start_ns = end_ns - 300 * int(1e9)  # last 5 minutes
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{_LOKI_BASE_URL}/loki/api/v1/query_range",
                    params={
                        "query": loki_query,
                        "start": start_ns,
                        "end": end_ns,
                        "limit": last_n,
                        "direction": "backward",
                    },
                )
                data = resp.json()
                streams = data.get("data", {}).get("result", [])
                lines = []
                for stream in streams:
                    for ts, line in stream.get("values", []):
                        lines.append({"ts": ts, "line": line[:500]})  # cap line length
                return {"service": service, "level": level, "lines": lines[:last_n]}
        except Exception as exc:
            return {"error": str(exc), "service": service}

    async def _check_service_health(self, inp: dict) -> dict:
        service = inp.get("service", "")
        port = _AI_PORTS.get(service)
        if port:
            url = f"http://{_GPU_HOST}:{port}/health"
        else:
            return {"service": service, "status": "no_health_endpoint", "note": "k8s-native service"}

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url)
                body = {}
                try:
                    body = resp.json()
                except Exception:
                    body = {"raw": resp.text[:200]}
                return {
                    "service": service,
                    "http_status": resp.status_code,
                    "status": "healthy" if resp.status_code < 400 else "degraded",
                    "detail": _redact(body),
                }
        except Exception as exc:
            return {"service": service, "status": "unreachable", "error": str(exc)}

    async def _query_incident_history(self, inp: dict) -> dict:
        service = inp.get("service", "")
        limit = min(int(inp.get("limit", 5)), 20)

        if self._incident_repo is None:
            return {"error": "incident history unavailable"}

        try:
            recent = self._incident_repo.list_recent(limit=50)
            matching = [
                {
                    "incident_id": inc.incident_id[:8],
                    "title": inc.title,
                    "severity": str(inc.severity),
                    "status": str(inc.status),
                    "detected_at": inc.detected_at.isoformat(),
                    "total_downtime_s": inc.total_downtime_s,
                    "root_cause": inc.root_cause,
                    "affected_services": list(inc.affected_services),
                }
                for inc in recent
                if service in inc.affected_services
            ][:limit]
            return {"service": service, "incidents": matching}
        except Exception as exc:
            return {"error": str(exc)}

    async def _query_redis_info(self, inp: dict) -> dict:
        import os
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        section = inp.get("section", "stats")  # memory, stats, clients, replication
        safe_sections = {"memory", "stats", "clients", "replication", "server"}
        if section not in safe_sections:
            return {"error": f"section '{section}' not allowed", "allowed": list(safe_sections)}
        try:
            import redis as redis_lib
            client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
            info = client.info(section=section)
            client.close()
            # redact any connection strings in values
            return {"section": section, "info": _redact(info)}
        except ImportError:
            return {"error": "redis library not available"}
        except Exception as exc:
            return {"error": str(exc)}


# Tool definitions for Claude's tool_use API
TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "query_metrics",
        "description": (
            "Query a pre-approved Prometheus metric for the current incident. "
            "Use this to get real-time latency, error rates, and resource usage. "
            f"Available metric_keys: {list(_ALLOWED_METRIC_QUERIES.keys())}"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric_key": {
                    "type": "string",
                    "description": "One of the pre-approved metric keys",
                }
            },
            "required": ["metric_key"],
        },
    },
    {
        "name": "query_logs",
        "description": (
            "Fetch recent log lines from a service. Logs are filtered by level. "
            "Returns up to 200 lines from the last 5 minutes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Service name (e.g. stt, llm, tts)"},
                "last_n_lines": {"type": "integer", "description": "Number of lines to fetch (max 200)", "default": 50},
                "level": {"type": "string", "description": "Log level filter: error, warn, info", "default": "error"},
            },
            "required": ["service"],
        },
    },
    {
        "name": "check_service_health",
        "description": "Check the current health status of a service via its /health endpoint.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Service name: stt, llm, tts, or a k8s service"},
            },
            "required": ["service"],
        },
    },
    {
        "name": "query_incident_history",
        "description": "Retrieve recent past incidents for a service to identify recurring patterns.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Service name to look up history for"},
                "limit": {"type": "integer", "description": "Max incidents to return (max 20)", "default": 5},
            },
            "required": ["service"],
        },
    },
    {
        "name": "query_redis_info",
        "description": "Get Redis operational statistics (memory, stats, clients, replication, server).",
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": "Redis INFO section: memory | stats | clients | replication | server",
                    "default": "stats",
                }
            },
            "required": [],
        },
    },
]


__all__ = ["EvidenceCollector", "TOOL_DEFINITIONS"]
