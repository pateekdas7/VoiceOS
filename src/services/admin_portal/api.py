"""AdminAPI -- the Administration Portal backend (V5 Ch13, Sprint-025.md).

Built as a Starlette ASGI app (not literal FastAPI -- see
``src.services.api_platform.api``'s module docstring for why). No
standalone HTTP listener is started by this sprint (Sprint-026 wires
uvicorn), same "library-class service" precedent as every service since
Sprint-013.

Auth: reuses :class:`AuthService`/:class:`AuthMiddleware` configured with
only a ``jwt_validator`` (Bearer JWT is the only credential AdminAPI
accepts). Authorization: Sprint-025.md states plainly "JWT with ADMIN or
SUPERVISOR role required" -- a direct role check, gated by
:class:`AdminRoleGateMiddleware`, not a new fine-grained permission
constant (``RBACEngine``'s existing permission vocabulary has no
"admin portal" resource class -- the sprint's own AC is phrased as a role
check, so that's what's enforced, literally).

All admin mutations are audited (Sprint-025.md AC) -- enforced mechanically
by :class:`AuditMiddleware` for every non-GET request that completes with a
2xx status, the same "one mechanical enforcement point" pattern as AR-8
tenant scoping in ``BaseRepository``.

Architecture: V5 Ch13 (Administration Portal).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from src.libs.contracts.primitives import CampaignId, TenantId
from src.services.ai_config.prompt_versioning import PromptImmutableError
from src.services.api_platform.api_key_lifecycle import APIKeyNotFoundError
from src.services.auth.middleware import AuthMiddleware
from src.services.auth.service import AuthService
from src.services.authz.roles import Role

from . import metrics
from .ai_config_admin import AIConfigAdminController
from .api_key_admin import APIKeyAdminController
from .audit_admin import AdminAuditViewNotConfiguredError, AuditAdminController
from .billing_admin import BillingAdminController, UsageReportingNotConfiguredError
from .campaign_admin import CampaignAdminController, CampaignApprovalDeniedError
from .tenant_admin import TenantAdminController
from .user_admin import InvalidSSOProviderError, SSONotConfiguredError, UserAdminController

_API_KEY_ADMIN_NOT_CONFIGURED = "no APIKeyAdminController wired into this AdminAPI"

if TYPE_CHECKING:
    from src.libs.audit.logger import AuditLogger

ADMIN_ROLES = frozenset({Role.ADMIN.value, Role.SUPERVISOR.value})


class AdminRoleGateMiddleware:
    """Rejects any request whose JWT role is not ADMIN or SUPERVISOR (Sprint-025.md AC)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        auth_context = scope.get("state", {}).get("auth_context")
        if auth_context is None or auth_context.role not in ADMIN_ROLES:
            metrics.record_rbac_denial()
            response = JSONResponse({"detail": "admin portal requires ADMIN or SUPERVISOR role"}, status_code=403)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class AuditMiddleware:
    """Records Administration metrics for every request, and an immutable audit
    entry for every non-GET request that completes with a 2xx status."""

    def __init__(self, app: ASGIApp, audit_logger: AuditLogger | None) -> None:
        self.app = app
        self._audit_logger = audit_logger

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        status_holder: dict[str, int] = {}

        async def _send(message: Any) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        await self.app(scope, receive, _send)

        status = status_holder.get("status", 0)
        metrics.record_mutation(scope["path"], status)

        if scope["method"] == "GET" or self._audit_logger is None:
            return

        auth_context = scope.get("state", {}).get("auth_context")
        if auth_context is not None and 200 <= status < 300:
            self._audit_logger.record(
                TenantId(auth_context.tenant_id),
                auth_context.subject,
                "admin_portal.mutation",
                "AdminAPI",
                scope["path"],
                "SUCCESS",
                event_payload={"method": scope["method"], "path": scope["path"], "status": status},
            )


def create_admin_api(
    *,
    auth_service: AuthService,
    tenant_admin: TenantAdminController,
    user_admin: UserAdminController,
    campaign_admin: CampaignAdminController,
    billing_admin: BillingAdminController,
    audit_admin: AuditAdminController,
    ai_config_admin: AIConfigAdminController,
    api_key_admin: APIKeyAdminController | None = None,
    audit_logger: AuditLogger | None = None,
) -> Starlette:
    """Assemble the Admin Portal Starlette app, base path ``/admin/v1`` (V5 Ch13)."""

    def _tenant_id(request: Request) -> TenantId:
        return TenantId(request.state.auth_context.tenant_id)

    def _actor(request: Request) -> str:
        return str(request.state.auth_context.subject)

    async def list_tenants(request: Request) -> JSONResponse:
        tenants = tenant_admin.list_tenants(_tenant_id(request))
        return JSONResponse([t.model_dump(mode="json") for t in tenants])

    async def suspend_tenant(request: Request) -> JSONResponse:
        tenant = tenant_admin.suspend(_tenant_id(request), _actor(request))
        return JSONResponse(tenant.model_dump(mode="json"))

    async def reactivate_tenant(request: Request) -> JSONResponse:
        tenant = tenant_admin.reactivate(_tenant_id(request), _actor(request))
        return JSONResponse(tenant.model_dump(mode="json"))

    async def list_users(request: Request) -> JSONResponse:
        users = user_admin.list_users(_tenant_id(request))
        return JSONResponse([u.model_dump(mode="json") for u in users])

    async def invite_user(request: Request) -> JSONResponse:
        body = await request.json()
        invitation = user_admin.invite_user(
            _tenant_id(request),
            body["email"],
            body["role_id"],
            body.get("scope_type", "TENANT"),
            _actor(request),
            body.get("scope_id", ""),
        )
        return JSONResponse(
            {"email": invitation.invitation.email, "expires_at": invitation.invitation.expires_at.isoformat()},
            status_code=201,
        )

    async def deactivate_user(request: Request) -> JSONResponse:
        user = user_admin.deactivate_user(_tenant_id(request), request.path_params["id"])
        return JSONResponse(user.model_dump(mode="json"))

    async def list_campaigns(request: Request) -> JSONResponse:
        campaigns = campaign_admin.list_active_campaigns(_tenant_id(request))
        return JSONResponse([c.model_dump(mode="json") for c in campaigns])

    async def approve_campaign(request: Request) -> JSONResponse:
        try:
            campaign = campaign_admin.approve(
                _tenant_id(request), CampaignId(request.path_params["id"]), _actor(request)
            )
        except CampaignApprovalDeniedError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=403)
        return JSONResponse(campaign.model_dump(mode="json"))

    async def get_subscription(request: Request) -> JSONResponse:
        subscription = billing_admin.get_subscription(_tenant_id(request))
        if subscription is None:
            return JSONResponse({"detail": "no subscription on record"}, status_code=404)
        return JSONResponse(subscription.model_dump(mode="json"))

    async def list_invoices(request: Request) -> JSONResponse:
        invoices = billing_admin.list_invoices(_tenant_id(request))
        return JSONResponse([inv.model_dump(mode="json") for inv in invoices])

    async def get_usage_summary(request: Request) -> JSONResponse:
        period_start = datetime.fromisoformat(request.query_params["period_start"])
        period_end = datetime.fromisoformat(request.query_params["period_end"])
        try:
            summary = billing_admin.usage_summary(_tenant_id(request), period_start, period_end)
        except UsageReportingNotConfiguredError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=501)
        return JSONResponse({usage_type.value: quantity for usage_type, quantity in summary.items()})

    async def search_audit(request: Request) -> JSONResponse:
        resource_type = request.query_params.get("resource_type")
        resource_id = request.query_params.get("resource_id")
        if resource_type and resource_id:
            events = audit_admin.by_resource(_tenant_id(request), resource_type, resource_id)
        else:
            events = audit_admin.in_range(_tenant_id(request))
        return JSONResponse([e.model_dump(mode="json") for e in events])

    async def get_compliance_report(request: Request) -> JSONResponse:
        tenant_name = request.query_params.get("tenant_name", str(_tenant_id(request)))
        report = audit_admin.compliance_report(_tenant_id(request), tenant_name)
        return JSONResponse(
            {
                "title": report.title,
                "columns": list(report.columns),
                "rows": [list(row) for row in report.rows],
                "generated_at": report.generated_at.isoformat(),
            }
        )

    async def list_admin_actions(request: Request) -> JSONResponse:
        try:
            events = audit_admin.list_admin_actions(_tenant_id(request))
        except AdminAuditViewNotConfiguredError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=501)
        return JSONResponse([e.model_dump(mode="json") for e in events])

    async def configure_sso(request: Request) -> JSONResponse:
        body = await request.json()
        try:
            user_admin.configure_sso(_tenant_id(request), body["provider"], body.get("config", {}))
        except InvalidSSOProviderError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)
        except SSONotConfiguredError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=501)
        return JSONResponse({"detail": "SSO configuration recorded"}, status_code=202)

    async def create_prompt_version(request: Request) -> JSONResponse:
        body = await request.json()
        version = ai_config_admin.create_prompt_version(
            _tenant_id(request), body["name"], body["template"], body.get("language", "en")
        )
        return JSONResponse(version.model_dump(mode="json"), status_code=201)

    async def publish_prompt_version(request: Request) -> JSONResponse:
        try:
            version = ai_config_admin.publish_prompt_version(_tenant_id(request), request.path_params["id"])
        except PromptImmutableError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)
        return JSONResponse(version.model_dump(mode="json"))

    async def edit_prompt_version(request: Request) -> Response:
        body = await request.json()
        try:
            version = ai_config_admin.edit_prompt_version(
                _tenant_id(request), request.path_params["id"], body["template"]
            )
        except PromptImmutableError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)
        return JSONResponse(version.model_dump(mode="json"))

    async def list_api_keys(request: Request) -> JSONResponse:
        if api_key_admin is None:
            return JSONResponse({"detail": _API_KEY_ADMIN_NOT_CONFIGURED}, status_code=501)
        keys = api_key_admin.list_keys(_tenant_id(request))
        return JSONResponse([{**k.model_dump(mode="json"), "key_hash": None} for k in keys])

    async def issue_api_key(request: Request) -> JSONResponse:
        if api_key_admin is None:
            return JSONResponse({"detail": _API_KEY_ADMIN_NOT_CONFIGURED}, status_code=501)
        body = await request.json()
        expires_at = datetime.fromisoformat(body["expires_at"]) if body.get("expires_at") else None
        raw_key, record = api_key_admin.issue_key(
            _tenant_id(request),
            _actor(request),
            role=body.get("role", ""),
            scopes=tuple(body.get("scopes", ())),
            plan_tier=body.get("plan_tier", ""),
            expires_at=expires_at,
        )
        return JSONResponse(
            {"api_key_id": record.api_key_id, "raw_key": raw_key, "plan_tier": record.plan_tier}, status_code=201
        )

    async def rotate_api_key(request: Request) -> JSONResponse:
        if api_key_admin is None:
            return JSONResponse({"detail": _API_KEY_ADMIN_NOT_CONFIGURED}, status_code=501)
        try:
            raw_key = api_key_admin.rotate_key(_tenant_id(request), request.path_params["id"], _actor(request))
        except APIKeyNotFoundError:
            return JSONResponse({"detail": "api key not found"}, status_code=404)
        return JSONResponse({"raw_key": raw_key})

    async def revoke_api_key(request: Request) -> JSONResponse:
        if api_key_admin is None:
            return JSONResponse({"detail": _API_KEY_ADMIN_NOT_CONFIGURED}, status_code=501)
        try:
            api_key_admin.revoke_key(_tenant_id(request), request.path_params["id"], _actor(request))
        except APIKeyNotFoundError:
            return JSONResponse({"detail": "api key not found"}, status_code=404)
        return JSONResponse({"detail": "api key revoked"})

    routes = [
        Route("/admin/v1/tenants", list_tenants, methods=["GET"]),
        Route("/admin/v1/tenants/suspend", suspend_tenant, methods=["POST"]),
        Route("/admin/v1/tenants/reactivate", reactivate_tenant, methods=["POST"]),
        Route("/admin/v1/users", list_users, methods=["GET"]),
        Route("/admin/v1/users/invite", invite_user, methods=["POST"]),
        Route("/admin/v1/users/{id}/deactivate", deactivate_user, methods=["POST"]),
        Route("/admin/v1/users/sso", configure_sso, methods=["POST"]),
        Route("/admin/v1/campaigns", list_campaigns, methods=["GET"]),
        Route("/admin/v1/campaigns/{id}/approve", approve_campaign, methods=["POST"]),
        Route("/admin/v1/billing/subscription", get_subscription, methods=["GET"]),
        Route("/admin/v1/billing/invoices", list_invoices, methods=["GET"]),
        Route("/admin/v1/billing/usage", get_usage_summary, methods=["GET"]),
        Route("/admin/v1/audit", search_audit, methods=["GET"]),
        Route("/admin/v1/audit/compliance-report", get_compliance_report, methods=["GET"]),
        Route("/admin/v1/audit/admin-actions", list_admin_actions, methods=["GET"]),
        Route("/admin/v1/ai-config/prompt-versions", create_prompt_version, methods=["POST"]),
        Route("/admin/v1/ai-config/prompt-versions/{id}/publish", publish_prompt_version, methods=["POST"]),
        Route("/admin/v1/ai-config/prompt-versions/{id}", edit_prompt_version, methods=["PATCH"]),
        Route("/admin/v1/api-keys", list_api_keys, methods=["GET"]),
        Route("/admin/v1/api-keys", issue_api_key, methods=["POST"]),
        Route("/admin/v1/api-keys/{id}/rotate", rotate_api_key, methods=["POST"]),
        Route("/admin/v1/api-keys/{id}/revoke", revoke_api_key, methods=["POST"]),
    ]

    middleware = [
        Middleware(AuthMiddleware, auth_service=auth_service),
        Middleware(AdminRoleGateMiddleware),
        Middleware(AuditMiddleware, audit_logger=audit_logger),
    ]

    return Starlette(routes=routes, middleware=middleware)


__all__ = ["ADMIN_ROLES", "create_admin_api"]
