"""HumanReviewAPI — REST endpoints for the human review UI (V4 Ch15).

Follows the same "library ASGI app, not bound to a live listener" pattern
as ``src.libs.health.aggregator.create_health_app()`` (Sprint-016) — every
VoiceOS service remains a library class until Sprint-026 K8s/Helm (see
CPU_NODE_STATE.md §8.1/TT-006). ``create_review_api()`` returns a Starlette
app exercised via ``starlette.testclient.TestClient`` in tests today.

Authorization: reads ``request.state.auth_context`` if an upstream
``AuthMiddleware`` (Sprint-018) has populated it; otherwise falls back to
``X-Actor-Role``/``X-Actor-Id`` headers. Both paths funnel through the same
SUPERVISOR/ADMIN role check.

Architecture: V4 Ch15 (Human Oversight — Review API).
"""

from __future__ import annotations

from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from src.libs.contracts.primitives import TenantId
from src.services.authz.roles import Role

from .override_logger import OverrideLogger
from .queue import HITLQueue

_ALLOWED_ROLES = {Role.SUPERVISOR.value, Role.ADMIN.value}


def _resolve_actor(request: Request) -> tuple[str, str]:
    """Return ``(role, actor_id)`` from ``request.state.auth_context`` if present, else headers."""
    auth_context = getattr(request.state, "auth_context", None)
    if auth_context is not None:
        return str(getattr(auth_context, "role", "")), str(getattr(auth_context, "subject", "anonymous"))
    return request.headers.get("X-Actor-Role", ""), request.headers.get("X-Actor-Id", "anonymous")


def create_review_api(queue: HITLQueue, override_logger: OverrideLogger) -> Starlette:
    """Build the ``GET /hitl/queue`` / ``POST /hitl/items/{item_id}/decision`` ASGI app."""

    async def list_queue(request: Request) -> JSONResponse:
        role, _actor_id = _resolve_actor(request)
        if role not in _ALLOWED_ROLES:
            return JSONResponse({"error": "SUPERVISOR role required"}, status_code=403)
        tenant_id = request.query_params.get("tenant_id", "")
        if not tenant_id:
            return JSONResponse({"error": "tenant_id is required"}, status_code=400)
        items = queue.list_pending(TenantId(tenant_id))
        return JSONResponse([_serialize(item) for item in items])

    async def record_decision(request: Request) -> JSONResponse:
        role, actor_id = _resolve_actor(request)
        if role not in _ALLOWED_ROLES:
            return JSONResponse({"error": "SUPERVISOR role required"}, status_code=403)

        body: dict[str, Any] = await request.json()
        rationale = body.get("rationale", "")
        if not isinstance(rationale, str) or not rationale.strip():
            return JSONResponse({"error": "rationale is required"}, status_code=400)

        tenant_id = body.get("tenant_id", "")
        if not tenant_id:
            return JSONResponse({"error": "tenant_id is required"}, status_code=400)

        item_id = request.path_params["item_id"]
        decision = body.get("decision", "REVIEWED")
        supervisor_id = body.get("supervisor_id") or actor_id

        try:
            record = override_logger.record_decision(TenantId(tenant_id), item_id, supervisor_id, decision, rationale)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        return JSONResponse(
            {
                "hitl_decision_id": record.hitl_decision_id,
                "hitl_item_id": record.hitl_item_id,
                "decision": record.decision,
                "supervisor_id": record.supervisor_id,
            },
            status_code=200,
        )

    return Starlette(
        routes=[
            Route("/hitl/queue", list_queue, methods=["GET"]),
            Route("/hitl/items/{item_id}/decision", record_decision, methods=["POST"]),
        ]
    )


def _serialize(item: Any) -> dict[str, Any]:
    return {
        "hitl_item_id": item.hitl_item_id,
        "call_id": item.call_id,
        "reason": item.reason,
        "priority": item.priority.value,
        "status": item.status.value,
        "enqueued_at": item.enqueued_at.isoformat(),
        "sla_deadline_at": item.sla_deadline_at.isoformat(),
        "sla_breached": item.sla_breached,
    }
