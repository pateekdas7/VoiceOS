"""ASGI app exposing the Alertmanager webhook receiver (ADR-006 Sec 4/13.4).

Mirrors ``src/libs/health/aggregator.py::create_health_app()``'s minimal
Starlette-app pattern. This is the HTTP endpoint Alertmanager's own
``webhook_configs`` receiver (``monitoring/grafana/alerts/alertmanager.yml``)
POSTs to -- a new receiver entry pointing at this endpoint is the only
change required on the Alertmanager side; its existing PagerDuty/Slack/Jira
routing is untouched.

Zero dependency on ``reasoning/`` -- this app only ever calls into
``WebhookIngestService``/``AlertLifecycleService`` (``plumbing/``).
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .webhook_ingest import WebhookIngestService


def create_webhook_app(ingest: WebhookIngestService) -> Starlette:
    """Build the ``POST /webhooks/alertmanager`` ASGI app.

    Args:
        ingest: The service adapting Alertmanager's webhook payload into
            ``AlertLifecycleService.ingest()`` calls.
    """

    async def alertmanager_webhook(request: Request) -> JSONResponse:
        payload = await request.json()
        results = ingest.ingest_alertmanager_webhook(payload)
        return JSONResponse({"ingested": len(results)}, status_code=200)

    return Starlette(routes=[Route("/webhooks/alertmanager", alertmanager_webhook, methods=["POST"])])


__all__ = ["create_webhook_app"]
