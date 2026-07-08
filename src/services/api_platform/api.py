"""PublicAPI -- the spec-first public REST API (V5 Ch16, Sprint-025.md).

Built as a Starlette ASGI app (not literal FastAPI -- ``fastapi`` is not a
project dependency; Sprint-016 deliberately chose Starlette for exactly
this reason, see pyproject.toml's comment on the ``starlette`` entry). No
standalone HTTP listener is started by this sprint (Sprint-026 wires
uvicorn), same "library-class service" precedent as every service since
Sprint-013.

Auth: reuses :class:`~src.services.auth.service.AuthService` /
:class:`~src.services.auth.middleware.AuthMiddleware` configured with only
an ``api_key_validator`` (JWT/mTLS unconfigured) so ``X-API-Key`` is the
only accepted credential, exactly as every other credential-type
combination in this codebase is expressed -- optional constructor params,
not a parallel auth stack.

Error model: every error response (this module's own 400/404/422/429/403,
not :class:`AuthMiddleware`'s already-shared 401 shape) is a uniform
``{"error": {"code": ..., "message": ...}}`` envelope (Sprint-025.md:
"error model standardization").

Architecture: V5 Ch16 (API Platform); V4 Ch12 (API Security).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from src.libs.api_security.rate_limiter import APIRateLimiter
from src.libs.contracts.models.billing import Invoice, SubscriptionTier
from src.libs.contracts.models.campaign import AudienceCriteria, Campaign, RetryPolicy
from src.libs.contracts.models.customer import Customer
from src.libs.contracts.models.integration import WEBHOOK_EVENT_TYPES, WebhookRegistration
from src.libs.contracts.primitives import CampaignId, CustomerId, TenantId
from src.libs.redis_client.rate_limiter import RateLimiter
from src.services.auth.middleware import AuthMiddleware
from src.services.auth.models import AuthenticationError
from src.services.auth.service import AuthService
from src.services.policy_engine.decision import PolicyOutcome

from . import metrics
from .openapi import get_openapi_schema
from .rate_limits import BURST_WINDOW_SECONDS, burst_for_tier, rps_for_tier

if TYPE_CHECKING:
    from src.libs.contracts.models.integration import APIRateLimitConfig
    from src.services.policy_engine.service import PolicyEngineService

_DENIED_OUTCOMES = (PolicyOutcome.DENY, PolicyOutcome.FORBID)


class RateLimitRepositoryPort(Protocol):
    """Structural port onto ``APIRateLimitRepository`` (Sprint-025 Part-3) -- persisted,
    plan-editable rate-limit configuration replacing the hardcoded ``TIER_RPS_LIMITS``
    dict as the source of truth when wired ("billing plans determine API capabilities")."""

    def get(self, tier: str) -> APIRateLimitConfig | None: ...


class UsageRepositoryPort(Protocol):
    """Structural port onto ``APIKeyUsageRepository`` (Sprint-025 Part-3 API key lifecycle:
    "rate limit association")."""

    def record(self, tenant_id: TenantId, api_key_id: str, route: str, status_code: int) -> None: ...


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    """The Public API's uniform error envelope (Sprint-025.md: "error model standardization")."""
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code)


class CustomerPort(Protocol):
    def get(self, tenant_id: TenantId, customer_id: CustomerId) -> Customer | None: ...


class CallDispositionPort(Protocol):
    def get_by_call_id(self, tenant_id: TenantId, call_id: str) -> Any | None: ...


class CampaignPort(Protocol):
    def create(
        self,
        tenant_id: TenantId,
        name: str,
        audience_criteria: AudienceCriteria,
        retry_policy: RetryPolicy,
        created_by: str,
        **kwargs: Any,
    ) -> Campaign: ...

    def get(self, tenant_id: TenantId, campaign_id: CampaignId) -> Campaign | None: ...


class CampaignAnalyticsPort(Protocol):
    def ptp_rate(self, tenant_id: TenantId, campaign_id: CampaignId) -> float: ...

    def results_for(self, tenant_id: TenantId, campaign_id: CampaignId) -> tuple[Any, ...]: ...


class WebhookPort(Protocol):
    def register_endpoint(
        self, tenant_id: TenantId, url: str, event_types: tuple[str, ...], secret: str | None = None
    ) -> WebhookRegistration: ...


class InvoicePort(Protocol):
    def list_invoices(self, tenant_id: TenantId) -> tuple[Invoice, ...]: ...


class TierLookupPort(Protocol):
    def __call__(self, tenant_id: TenantId) -> SubscriptionTier | None: ...


class MetricsMiddleware:
    """Records ``PUBLIC_API_REQUESTS_TOTAL`` for every request, by route and response status
    (Sprint-025.md: "API metrics")."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

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
        metrics.record_request(scope["path"], status_holder.get("status", 0))


class RateLimitMiddleware:
    """Per-tenant, per-plan-tier rate limiting for the Public API (V4 Ch12 §12.13), with an
    optional PolicyEngine entitlement gate (Sprint-025.md: "PolicyEngine integration") and
    dual-window burst handling (Sprint-025 Part-3: sustained rps over a 1s window, plus a
    burst cap over a longer ``BURST_WINDOW_SECONDS`` window so a short spike isn't rejected
    at the strict per-second rate).

    Runs *after* :class:`AuthMiddleware` in the stack (Starlette applies
    middleware outer-to-inner in the order given to ``Middleware(...)``, so
    this is registered second) -- it needs ``scope["state"]["auth_context"]``
    already populated to know which tenant's budget to check.
    """

    def __init__(
        self,
        app: ASGIApp,
        rate_limiter: RateLimiter,
        tier_lookup: TierLookupPort,
        policy_engine: PolicyEngineService | None = None,
        rate_limit_repository: RateLimitRepositoryPort | None = None,
    ) -> None:
        self.app = app
        self._rate_limiter = rate_limiter
        self._tier_lookup = tier_lookup
        self._policy_engine = policy_engine
        self._rate_limit_repo = rate_limit_repository

    def _resolve_limits(self, tier: SubscriptionTier | None) -> tuple[int, int]:
        """(requests_per_second, burst_capacity) -- persisted ``api_rate_limits`` row when
        wired and present, else the hardcoded fallback ("billing plans determine API
        capabilities" backed by real rows, degrading gracefully when unwired/unseeded)."""
        if self._rate_limit_repo is not None:
            config = self._rate_limit_repo.get(tier.value if tier else "TRIAL")
            if config is not None:
                return config.requests_per_second, config.burst_capacity
        return rps_for_tier(tier), burst_for_tier(tier)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        auth_context = scope.get("state", {}).get("auth_context")
        if auth_context is not None:
            tenant_id = TenantId(auth_context.tenant_id)
            tier = self._tier_lookup(tenant_id)

            if self._policy_engine is not None:
                decision = self._policy_engine.check_entitlement(
                    tenant_id=str(tenant_id), feature="public_api", tier=tier.value if tier else "TRIAL"
                )
                if decision.outcome in _DENIED_OUTCOMES:
                    metrics.record_entitlement_denial()
                    response = _error(403, "ENTITLEMENT_DENIED", decision.reason)
                    await response(scope, receive, send)
                    return

            rps, burst = self._resolve_limits(tier)

            limiter = APIRateLimiter(self._rate_limiter, requests_per_second=rps)
            result = limiter.check(str(tenant_id))
            if not result.allowed:
                metrics.record_rate_limit_rejection()
                response = _error(429, "RATE_LIMIT_EXCEEDED", "rate limit exceeded")
                await response(scope, receive, send)
                return

            burst_result = self._rate_limiter.check(
                f"api-burst:{tenant_id}", limit=burst, window_seconds=BURST_WINDOW_SECONDS
            )
            if not burst_result.allowed:
                metrics.record_rate_limit_rejection()
                response = _error(429, "RATE_LIMIT_EXCEEDED", "burst limit exceeded")
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


class UsageLoggingMiddleware:
    """Records ``api_key_usage`` for every request authenticated by an API key
    (Sprint-025 Part-3 API key lifecycle: "audit logging", "rate limit association").

    A no-op for requests with no ``api_key_id`` (JWT/mTLS auth, or an
    in-memory-only key that was never persisted) and when unwired.
    """

    def __init__(self, app: ASGIApp, usage_repository: UsageRepositoryPort | None = None) -> None:
        self.app = app
        self._usage_repo = usage_repository

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._usage_repo is None:
            await self.app(scope, receive, send)
            return

        status_holder: dict[str, int] = {}

        async def _send(message: Any) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        await self.app(scope, receive, _send)

        auth_context = scope.get("state", {}).get("auth_context")
        if auth_context is not None and auth_context.api_key_id:
            self._usage_repo.record(
                TenantId(auth_context.tenant_id),
                auth_context.api_key_id,
                scope["path"],
                status_holder.get("status", 0),
            )


def create_public_api(
    *,
    auth_service: AuthService,
    rate_limiter: RateLimiter,
    tier_lookup: TierLookupPort,
    customer_repository: CustomerPort,
    call_disposition_repository: CallDispositionPort,
    campaign_service: CampaignPort,
    campaign_analytics: CampaignAnalyticsPort,
    webhook_service: WebhookPort,
    invoice_repository: InvoicePort,
    policy_engine: PolicyEngineService | None = None,
    rate_limit_repository: RateLimitRepositoryPort | None = None,
    usage_repository: UsageRepositoryPort | None = None,
) -> Starlette:
    """Assemble the Public API Starlette app (V5 Ch16)."""

    def _tenant_id(request: Request) -> TenantId:
        return TenantId(request.state.auth_context.tenant_id)

    async def _read_json(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
        """Parse the request body as JSON, or a 400 error response on malformed input
        (Sprint-025.md: "reject malformed payloads")."""
        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return None, _error(400, "MALFORMED_PAYLOAD", "request body is not valid JSON")
        return body, None

    async def get_customer(request: Request) -> JSONResponse:
        customer_id = CustomerId(request.path_params["id"])
        customer = customer_repository.get(_tenant_id(request), customer_id)
        if customer is None:
            return _error(404, "NOT_FOUND", "customer not found")
        return JSONResponse(customer.model_dump(mode="json"))

    async def get_call(request: Request) -> JSONResponse:
        call_id = request.path_params["id"]
        disposition = call_disposition_repository.get_by_call_id(_tenant_id(request), call_id)
        if disposition is None:
            return _error(404, "NOT_FOUND", "call not found")
        return JSONResponse(disposition.model_dump(mode="json"))

    async def create_campaign(request: Request) -> JSONResponse:
        body, error_response = await _read_json(request)
        if error_response is not None:
            return error_response
        assert body is not None
        campaign = campaign_service.create(
            _tenant_id(request),
            name=body["name"],
            audience_criteria=AudienceCriteria(),
            retry_policy=RetryPolicy(),
            created_by=f"api-key:{request.state.auth_context.subject}",
            description=body.get("description", ""),
            daily_start_hour=body.get("daily_start_hour", 9),
            daily_end_hour=body.get("daily_end_hour", 18),
            timezone=body.get("timezone", "Asia/Kolkata"),
        )
        return JSONResponse(campaign.model_dump(mode="json"), status_code=201)

    async def get_campaign_analytics(request: Request) -> JSONResponse:
        tenant_id = _tenant_id(request)
        campaign_id = CampaignId(request.path_params["id"])
        campaign = campaign_service.get(tenant_id, campaign_id)
        if campaign is None:
            return _error(404, "NOT_FOUND", "campaign not found")
        results = campaign_analytics.results_for(tenant_id, campaign_id)
        return JSONResponse(
            {
                "campaign_id": str(campaign_id),
                "calls_completed": len(results),
                "ptp_count": sum(1 for r in results if getattr(r, "ptp_created", False)),
                "ptp_rate": campaign_analytics.ptp_rate(tenant_id, campaign_id),
            }
        )

    async def register_webhook(request: Request) -> JSONResponse:
        body, error_response = await _read_json(request)
        if error_response is not None:
            return error_response
        assert body is not None
        event_types = tuple(body.get("event_types", ()))
        if not event_types or set(event_types) - set(WEBHOOK_EVENT_TYPES):
            return _error(422, "VALIDATION_ERROR", "invalid or missing event_types")
        if not body.get("url"):
            return _error(422, "VALIDATION_ERROR", "missing url")
        registration = webhook_service.register_endpoint(_tenant_id(request), body["url"], event_types)
        payload = registration.model_dump(mode="json")
        payload.pop("secret", None)
        return JSONResponse(payload, status_code=201)

    async def list_invoices(request: Request) -> JSONResponse:
        invoices = invoice_repository.list_invoices(_tenant_id(request))
        return JSONResponse([inv.model_dump(mode="json") for inv in invoices])

    async def openapi_json(_request: Request) -> JSONResponse:
        return JSONResponse(get_openapi_schema())

    protected_routes = [
        Route("/customers/{id}", get_customer, methods=["GET"]),
        Route("/calls/{id}", get_call, methods=["GET"]),
        Route("/campaigns", create_campaign, methods=["POST"]),
        Route("/campaigns/{id}/analytics", get_campaign_analytics, methods=["GET"]),
        Route("/webhooks", register_webhook, methods=["POST"]),
        Route("/invoices", list_invoices, methods=["GET"]),
    ]
    protected_middleware = [
        Middleware(AuthMiddleware, auth_service=auth_service),
        Middleware(
            RateLimitMiddleware,
            rate_limiter=rate_limiter,
            tier_lookup=tier_lookup,
            policy_engine=policy_engine,
            rate_limit_repository=rate_limit_repository,
        ),
        Middleware(MetricsMiddleware),
        Middleware(UsageLoggingMiddleware, usage_repository=usage_repository),
    ]
    protected_app = Starlette(routes=protected_routes, middleware=protected_middleware)

    # /openapi.json is deliberately unauthenticated (V5 Ch16 health-check
    # requirement, CPU_NODE_STATE.md §14: "OpenAPI spec accessible check")
    # -- mounted as a sibling, not inside the AuthMiddleware-wrapped app.
    return Starlette(
        routes=[
            Route("/openapi.json", openapi_json, methods=["GET"]),
            Mount("/", app=protected_app),
        ]
    )


__all__ = ["AuthenticationError", "create_public_api"]
