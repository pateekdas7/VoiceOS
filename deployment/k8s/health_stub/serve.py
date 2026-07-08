"""Minimal K8s deployment entrypoint reusing the existing Sprint-016 health
infrastructure (src/libs/health) unchanged, plus (Sprint-027) a real
Prometheus `/metrics` exposition endpoint via the standard
`prometheus_client` exposition format (the same library every service's
own `REDMetrics`/`get_red_metrics()` -- src/libs/observability/metrics.py
-- already uses).

Every VoiceOS service remains a library class with no standalone business
HTTP API (TT-006, CPU_NODE_STATE.md Sec8.1) -- this entrypoint does not
change that, and `/metrics` here exposes only the health-stub process's
own default collector metrics (Python GC/process stats), not real
per-service business metrics (those need TT-006 resolved first, i.e. a
real HTTP-serving process per service). It exists so Prometheus's own
scrape path (Sprint-027) can be proven genuinely functional -- pod
reachable, `/metrics` returns valid exposition-format text, target shows
`up` in Prometheus -- against a real Running pod, the same "prove the
infrastructure path, not business logic" precedent Sprint-026 established
for `/health/live`/`/health/ready`.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

import uvicorn
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from src.libs.health.aggregator import create_health_app
from src.libs.health.probe import LivenessProbe, ReadinessProbe

liveness = LivenessProbe()
readiness = ReadinessProbe(liveness)
app = create_health_app(liveness, readiness)

# One structured JSON log line at startup (Sprint-027), byte-for-byte the
# same schema as StructuredLogger (src/libs/observability/logger.py) --
# constructed by hand here rather than importing that module, since
# StructuredLogger's package (src.libs.observability) eagerly imports
# OTelTracer, which needs the opentelemetry SDK, deliberately not staged
# into this minimal image (same "no new business logic, no new heavy
# dependency" discipline as the rest of this health-stub). Proves the
# FluentBit -> Loki pipeline end-to-end against a genuine JSON log line
# with the indexed fields (tenant_id/service/call_id/trace_id/level).
# `call_id="test-call-001"` matches the worked example in
# monitoring/logging/log_query_examples.md. `SERVICE_NAME` is already
# injected by every chart's ConfigMap (`.Chart.Name`, Sprint-026).
print(
    json.dumps(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": "INFO",
            "service": os.environ.get("SERVICE_NAME", "health-stub"),
            "tenant_id": "",
            "call_id": "test-call-001",
            "trace_id": "test-trace-001",
            "correlation_id": "",
            "message": "health-stub process started",
        }
    ),
    file=sys.stdout,
    flush=True,
)


async def metrics(_request: Request) -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.router.routes.append(Route("/metrics", metrics))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
