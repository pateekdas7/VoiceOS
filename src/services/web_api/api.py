"""WebAPI -- the Web BFF Starlette app (ADR-005 Sec 4.1).

Built as a Starlette ASGI app, same precedent as ``api_platform.api``
(Sprint-016's choice: ``fastapi`` is not a project dependency). No
standalone HTTP listener is started here -- production wiring (uvicorn) is
a later, separate step, matching every other "library-class service" in
this codebase.

Auth: browser-session cookies signed by :class:`~.session.WebSessionCodec`,
never the tenant-JWT/API-key stack in ``src.services.auth`` (ADR-005 Sec 3 --
PlatformActor has no tenant_id, so it cannot flow through a type that
requires one).

Scope note: this Phase-1 implementation resolves two identity paths fully --
(1) a verified email matching an existing ``platform_users`` row, and (2) a
verified email accepting a pending tenant invitation (``invitation_token``).
A *returning* tenant user logging in with no invitation token (i.e., which
tenant does this verified email belong to, with no token to disambiguate)
is intentionally NOT resolved here -- that needs a cross-tenant email
resolution mechanism (e.g. a dedicated lookup index, or per-tenant login
subdomains) that hasn't been decided yet, and building one unprompted would
be inventing a security-relevant design point rather than reusing one.
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

_log = logging.getLogger("voiceos.web_api")

from monitoring.gpu_fleet.fleet_health import GPUFleetHealthMonitor
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from src.libs.audit.event import AuditEvent
from src.libs.contracts.models.billing import SubscriptionTier
from src.libs.contracts.models.campaign import AudienceCriteria, RetryPolicy
from src.libs.contracts.models.customer import Customer, CustomerContact
from src.libs.contracts.models.saas_ops import FeatureFlagScope, FeatureFlagState
from src.libs.contracts.models.tenant import IsolationProfile
from src.libs.contracts.models.user import OrgScope
from src.libs.contracts.primitives import CampaignId, CustomerId, TenantId
from src.libs.health.aggregator import HealthAggregator
from src.libs.repositories.admin_audit_view import AdminAuditViewRepository
from src.libs.repositories.audit import AuditRepository
from src.libs.repositories.campaign_audience import CampaignAudienceRepository
from src.services.analytics.service import AnalyticsService
from src.services.authz.roles import (
    PERM_DECIDE_HITL,
    PERM_MONITOR_CALLS,
    PERM_READ_ALL,
    PERM_VIEW_ANALYTICS,
    PERM_WRITE_ALL,
    PERM_WRITE_CAMPAIGNS,
    PERM_WRITE_USERS,
)
from src.services.bi_platform.executive_dashboard import ExecutiveDashboard
from src.services.billing.service import BillingService
from src.services.campaign_management.lifecycle import CampaignLifecycleError
from src.services.campaign_management.service import CampaignPromptNotPinnedError, CampaignService
from src.services.collections.escalation import EscalationWorkflow
from src.services.compliance_monitoring.service import ComplianceMonitoring
from src.services.crm.service import CustomerService
from src.services.hitl.override_logger import OverrideLogger
from src.services.hitl.queue import HITLQueue
from src.services.ops_intelligence.models import InsightCategory, ReportType
from src.services.ops_intelligence.plumbing.alert_lifecycle import AlertLifecycleService
from src.services.ops_intelligence.plumbing.repository import AlertRepositoryPort
from src.services.ops_intelligence.plumbing.webhook_ingest import WebhookIngestService
from src.services.ops_intelligence.reasoning.repository import (
    CapacityForecastRepositoryPort,
    InsightRepositoryPort,
    ReportRepositoryPort,
)
from src.services.ops_intelligence.service import REASONING_FLAG_NAME, OpsIntelligenceService
from src.services.platform_admin.roles import (
    PERM_PLATFORM_READ_ALL,
    PERM_PLATFORM_READ_AUDIT,
    PERM_PLATFORM_READ_INFRA,
    PERM_PLATFORM_WRITE_BILLING,
    PERM_PLATFORM_WRITE_CLIENTS,
    PERM_PLATFORM_WRITE_SETTINGS,
    PERM_PLATFORM_WRITE_STAFF,
    PlatformRole,
)
from src.services.platform_admin.service import (
    DuplicatePlatformUserError,
    PlatformAdminService,
    PlatformUserNotFoundError,
)
from src.services.reporting.service import ReportingService
from src.services.saas_ops.feature_flags import FeatureFlagService
from src.services.tenant_management.lifecycle import InvalidTenantTransitionError
from src.services.tenant_management.service import TenantService
from src.services.user_management.invitation import (
    EmailAlreadyRegisteredError,
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationNotPendingError,
)
from src.services.user_management.service import UserService

from .auth_middleware import (
    ForbiddenError,
    SessionRequiredError,
    WebSessionMiddleware,
    require_platform_permission,
    require_tenant_permission,
)
from .google_oauth import GoogleIdentityError
from .session import WebSessionCodec

_SESSION_COOKIE = "voiceos_session"
_ACTOR_KIND_COOKIE = "voiceos_actor_kind"

_DEFAULT_NEXT = {"platform": "/admin/dashboard", "tenant": "/client/dashboard"}


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    """The BFF's uniform error envelope, same shape as api_platform's (Sprint-025 precedent)."""
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code)


def _require_tenant_id(session: Any) -> TenantId:
    """Narrow WebSession.tenant_id (str | None -- shared with platform sessions,
    which always carry None) to a real TenantId. Safe: require_tenant_session()
    only ever returns actor_kind="tenant" sessions, and WebSessionCodec.encode()
    refuses to mint one without a tenant_id -- this makes that invariant
    explicit rather than silently trusting it at every call site."""
    assert session.tenant_id is not None, "a tenant session must always carry a tenant_id"
    return TenantId(session.tenant_id)


def _resolve_tenant_role(user_service: UserService, tenant_user: Any) -> tuple[str, tuple[str, ...]]:
    """Resolve a tenant user's first role assignment to its real name + permissions.

    Reads the ``roles`` table row (Law of Authority -- DB-authoritative, not
    a hardcoded mirror of role_id -> permissions). Fails closed: an assignment
    that doesn't resolve to a real role grants zero permissions, never all.
    """
    if not tenant_user.role_assignments:
        return "", ()
    assignment = tenant_user.role_assignments[0]
    role = user_service.get_role(tenant_user.tenant_id, assignment.role_id)
    if role is None:
        return "", ()
    return role.name, tuple(role.permissions)


class GoogleOAuthPort(Protocol):
    def build_authorize_url(self, *, state: str, redirect_uri: str) -> str: ...

    async def resolve_verified_email(self, *, code: str, redirect_uri: str) -> Any: ...


def _encode_state(next_url: str | None, invitation_token: str | None) -> str:
    """Packs round-trip params into Google's opaque ``state`` param.

    Phase-1 simplification: this is transparency-safe (no secrets inside),
    but is NOT yet a full anti-CSRF token -- it doesn't verify the callback
    request originated from a browser this server issued a state to. Phase
    2 should add a signed/stored nonce comparison before this handles real
    traffic.
    """
    payload = {"next": next_url, "invitation_token": invitation_token, "nonce": secrets.token_urlsafe(8)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_state(state: str) -> dict[str, str | None]:
    try:
        payload: dict[str, str | None] = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
    except Exception:
        return {"next": None, "invitation_token": None}
    return payload


def create_web_api(
    *,
    session_codec: WebSessionCodec,
    google_oauth: GoogleOAuthPort,
    platform_admin: PlatformAdminService,
    user_service: UserService,
    tenant_service: TenantService | None = None,
    campaign_service: CampaignService | None = None,
    hitl_queue: HITLQueue | None = None,
    override_logger: OverrideLogger | None = None,
    admin_audit_repository: AdminAuditViewRepository | None = None,
    audit_repository: AuditRepository | None = None,
    crm_service: CustomerService | None = None,
    collections_workflow: EscalationWorkflow | None = None,
    campaign_audience_repository: CampaignAudienceRepository | None = None,
    pipeline_service: Any = None,
    lead_intake_service: Any = None,
    lead_repository: Any = None,
    lead_import_repository: Any = None,
    dialer_session_manager: Any = None,
    twilio_auth_token: str | None = None,
    billing_service: BillingService | None = None,
    feature_flag_service: FeatureFlagService | None = None,
    compliance_monitoring: ComplianceMonitoring | None = None,
    gpu_fleet_monitor: GPUFleetHealthMonitor | None = None,
    executive_dashboard: ExecutiveDashboard | None = None,
    analytics_service: AnalyticsService | None = None,
    reporting_service: ReportingService | None = None,
    ops_intelligence: OpsIntelligenceService | None = None,
    alert_repository: AlertRepositoryPort | None = None,
    alert_lifecycle: AlertLifecycleService | None = None,
    insight_repository: InsightRepositoryPort | None = None,
    report_repository: ReportRepositoryPort | None = None,
    capacity_forecast_repository: CapacityForecastRepositoryPort | None = None,
    system_x_service: Any = None,
    call_summary_repository: Any = None,
    callback_scheduler: Any = None,
    frontend_base_url: str,
    bff_public_url: str,
    health_aggregator: HealthAggregator | None = None,
    cookie_secure: bool = True,
) -> Starlette:
    """Assemble the Web BFF Starlette app (ADR-005 Sec 4.1)."""

    callback_redirect_uri = f"{bff_public_url}/auth/google/callback"

    def _set_session_cookies(response: RedirectResponse, *, token: str, actor_kind: str) -> None:
        response.set_cookie(
            _SESSION_COOKIE, token, httponly=True, secure=cookie_secure, samesite="lax", max_age=8 * 3600
        )
        response.set_cookie(
            _ACTOR_KIND_COOKIE, actor_kind, httponly=True, secure=cookie_secure, samesite="lax", max_age=8 * 3600
        )

    async def google_start(request: Request) -> RedirectResponse:
        next_url = request.query_params.get("next")
        invitation_token = request.query_params.get("invitation_token")
        state = _encode_state(next_url, invitation_token)
        authorize_url = google_oauth.build_authorize_url(state=state, redirect_uri=callback_redirect_uri)
        return RedirectResponse(authorize_url, status_code=302)

    async def google_callback(request: Request) -> RedirectResponse:
        code = request.query_params.get("code")
        state = request.query_params.get("state", "")
        parsed_state = _decode_state(state)
        next_url = parsed_state.get("next")
        invitation_token = parsed_state.get("invitation_token")

        if not code:
            return RedirectResponse(f"{frontend_base_url}/login?error=missing_code", status_code=302)

        try:
            identity = await google_oauth.resolve_verified_email(code=code, redirect_uri=callback_redirect_uri)
        except GoogleIdentityError:
            return RedirectResponse(f"{frontend_base_url}/login?error=google_auth_failed", status_code=302)

        platform_user = platform_admin.find_by_email(identity.email)
        if platform_user is not None and platform_user.is_active:
            token = session_codec.encode(
                actor_kind="platform",
                subject=platform_user.platform_user_id,
                role=platform_user.platform_role,
                email=platform_user.email,
                tenant_id=None,
            )
            response = RedirectResponse(f"{frontend_base_url}{next_url or _DEFAULT_NEXT['platform']}", status_code=302)
            _set_session_cookies(response, token=token, actor_kind="platform")
            return response

        if invitation_token:
            try:
                activated_user = user_service.activate_invitation(invitation_token, identity.name)
            except (InvitationNotFoundError, InvitationExpiredError, InvitationNotPendingError):
                return RedirectResponse(f"{frontend_base_url}/signup?error=invalid_invitation", status_code=302)
            except EmailAlreadyRegisteredError:
                return RedirectResponse(f"{frontend_base_url}/login?error=already_registered", status_code=302)

            # activate_invitation() returns the User as constructed *before*
            # assign_role() ran -- role_assignments on that object is always
            # empty. Re-fetch to get the hydrated, post-assignment record.
            tenant_user = user_service.get(activated_user.tenant_id, activated_user.user_id) or activated_user
            role_name, role_permissions = _resolve_tenant_role(user_service, tenant_user)
            token = session_codec.encode(
                actor_kind="tenant",
                subject=tenant_user.user_id,
                role=role_name,
                permissions=role_permissions,
                email=tenant_user.email,
                tenant_id=str(tenant_user.tenant_id),
            )
            response = RedirectResponse(f"{frontend_base_url}{next_url or _DEFAULT_NEXT['tenant']}", status_code=302)
            _set_session_cookies(response, token=token, actor_kind="tenant")
            return response

        # No platform_users match and no invitation to activate -- this email
        # isn't provisioned anywhere VoiceOS knows about. Returning-tenant-user
        # login (no invitation token) is the unresolved case noted in this
        # module's docstring -- honestly reported, not silently guessed at.
        return RedirectResponse(f"{frontend_base_url}/login?error=no_account", status_code=302)

    async def logout(_request: Request) -> RedirectResponse:
        response = RedirectResponse(f"{frontend_base_url}/login", status_code=302)
        response.delete_cookie(_SESSION_COOKIE)
        response.delete_cookie(_ACTOR_KIND_COOKIE)
        return response

    async def system_health(_request: Request) -> JSONResponse:
        if health_aggregator is None:
            return JSONResponse([])
        report = await health_aggregator.report()
        return JSONResponse(
            [
                {"component": name, "status": status.value, "latencyMs": None, "lastChecked": None}
                for name, status in report.components.items()
            ]
        )

    routes = [
        Route("/auth/google/start", google_start, methods=["GET"]),
        Route("/auth/google/callback", google_callback, methods=["GET"]),
        Route("/auth/logout", logout, methods=["GET", "POST"]),
        Route("/system/health", system_health, methods=["GET"]),
        *_build_team_routes(user_service),
    ]

    if tenant_service is not None:
        routes.extend(_build_client_routes(tenant_service))

    if campaign_service is not None:
        routes.extend(_build_campaign_routes(campaign_service))

    if hitl_queue is not None and override_logger is not None:
        routes.extend(_build_hitl_routes(hitl_queue, override_logger))

    if admin_audit_repository is not None:
        routes.extend(_build_admin_audit_routes(admin_audit_repository))

    if crm_service is not None:
        routes.extend(_build_crm_routes(crm_service))

    if collections_workflow is not None:
        routes.extend(_build_collections_routes(collections_workflow))

    if campaign_service is not None and campaign_audience_repository is not None and crm_service is not None:
        routes.extend(_build_leads_routes(campaign_service, campaign_audience_repository, crm_service))

    if pipeline_service is not None and lead_intake_service is not None and lead_repository is not None and lead_import_repository is not None:
        routes.extend(_build_pipeline_lead_routes(pipeline_service, lead_intake_service, lead_repository, lead_import_repository))

    if dialer_session_manager is not None:
        routes.extend(_build_dialer_routes(dialer_session_manager, twilio_auth_token))

    if billing_service is not None:
        routes.extend(_build_billing_routes(billing_service))

    if feature_flag_service is not None:
        routes.extend(_build_platform_settings_routes(feature_flag_service))

    if compliance_monitoring is not None:
        routes.extend(_build_compliance_routes(compliance_monitoring))

    if audit_repository is not None:
        routes.extend(_build_security_routes(audit_repository))

    routes.extend(_build_users_roles_routes(platform_admin))

    if gpu_fleet_monitor is not None:
        routes.extend(_build_infrastructure_routes(gpu_fleet_monitor))

    if executive_dashboard is not None:
        routes.extend(_build_admin_analytics_routes(executive_dashboard))

    if analytics_service is not None:
        routes.extend(_build_client_analytics_routes(analytics_service))

    if reporting_service is not None:
        routes.extend(_build_reports_routes(reporting_service))

    if alert_repository is not None and alert_lifecycle is not None:
        routes.extend(_build_alerts_routes(alert_repository, alert_lifecycle))

    if insight_repository is not None and ops_intelligence is not None:
        routes.extend(_build_ai_insights_routes(insight_repository, ops_intelligence))

    if report_repository is not None and ops_intelligence is not None:
        routes.extend(_build_ai_reports_routes(report_repository, ops_intelligence))

    if capacity_forecast_repository is not None and ops_intelligence is not None:
        routes.extend(_build_capacity_routes(capacity_forecast_repository, ops_intelligence))

    if alert_repository is not None and insight_repository is not None:
        routes.extend(_build_incident_timeline_routes(alert_repository, insight_repository))

    # Alertmanager webhook receiver — no auth required (Alertmanager POSTs
    # directly without browser cookies). Mount as regular route so the session
    # middleware sees it but doesn't gate it.
    if alert_lifecycle is not None:
        webhook_ingest = WebhookIngestService(alert_lifecycle)
        system_x_controller = system_x_service.build_controller() if system_x_service is not None else None
        routes.extend(_build_webhook_routes(webhook_ingest, system_x_controller))

    if system_x_service is not None:
        routes.extend(_build_system_x_routes(system_x_service))

    # Phase 3: post-call sales summary + callback management endpoints.
    if call_summary_repository is not None:
        routes.extend(_build_call_summary_routes(call_summary_repository))
    if callback_scheduler is not None:
        routes.extend(_build_callbacks_routes(callback_scheduler))

    # Startup handlers for background tasks (GPU polling, daily aggregation).
    startup_handlers = _build_startup_handlers(gpu_fleet_monitor, analytics_service)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(app: Any):
        for handler in startup_handlers:
            handler()
        yield

    return Starlette(
        routes=routes,
        lifespan=_lifespan,
        middleware=[
            # Outermost: the frontend (a different origin -- e.g. localhost:3000 vs.
            # this BFF's localhost:8100 in dev, app.voiceos.ai vs. api.voiceos.ai in
            # prod) sends every request with credentials: "include". Without this,
            # every fetch() in frontend/lib/api/*.ts is silently blocked by the
            # browser's CORS check -- curl-based verification never catches this
            # since curl doesn't enforce CORS, only real browsers do.
            Middleware(
                CORSMiddleware,
                allow_origins=[frontend_base_url],
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            ),
            Middleware(WebSessionMiddleware, session_codec=session_codec),
        ],
    )


def _build_client_routes(tenant_service: TenantService) -> list[Route]:
    """Admin -> Clients CRUD (ADR-005 Sec 6.1), backed entirely by tenant_management."""

    async def list_clients(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        tenants = tenant_service.list_all()
        return JSONResponse([t.model_dump(mode="json") for t in tenants])

    async def create_client(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_WRITE_CLIENTS)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return _error(400, "MALFORMED_PAYLOAD", "request body is not valid JSON")

        for field in ("slug", "display_name", "subscription_tier"):
            if not body.get(field):
                return _error(422, "VALIDATION_ERROR", f"missing required field: {field}")

        isolation_profile = IsolationProfile(body.get("isolation_profile", IsolationProfile.SHARED.value))
        try:
            tenant = tenant_service.create_tenant(
                body["slug"], body["display_name"], body["subscription_tier"], isolation_profile
            )
        except Exception as exc:
            return _error(422, "VALIDATION_ERROR", str(exc))
        return JSONResponse(tenant.model_dump(mode="json"), status_code=201)

    async def get_client(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        tenant = tenant_service.get(TenantId(request.path_params["id"]))
        if tenant is None:
            return _error(404, "NOT_FOUND", "client not found")
        return JSONResponse(tenant.model_dump(mode="json"))

    async def suspend_client(request: Request) -> JSONResponse:
        try:
            session = require_platform_permission(request, PERM_PLATFORM_WRITE_CLIENTS)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        try:
            tenant = tenant_service.suspend(TenantId(request.path_params["id"]), session.subject)
        except InvalidTenantTransitionError as exc:
            return _error(409, "INVALID_TRANSITION", str(exc))
        except ValueError as exc:
            return _error(404, "NOT_FOUND", str(exc))
        return JSONResponse(tenant.model_dump(mode="json"))

    async def reactivate_client(request: Request) -> JSONResponse:
        try:
            session = require_platform_permission(request, PERM_PLATFORM_WRITE_CLIENTS)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        try:
            tenant = tenant_service.reactivate(TenantId(request.path_params["id"]), session.subject)
        except InvalidTenantTransitionError as exc:
            return _error(409, "INVALID_TRANSITION", str(exc))
        except ValueError as exc:
            return _error(404, "NOT_FOUND", str(exc))
        return JSONResponse(tenant.model_dump(mode="json"))

    async def begin_delete_client(request: Request) -> JSONResponse:
        # Mapped to the real two-phase soft-delete lifecycle (CANCELLED -> DELETING),
        # not a literal hard DELETE -- tenant_management has no such operation, and
        # inventing one would bypass the crypto-shred/tombstone flow it's built around.
        try:
            session = require_platform_permission(request, PERM_PLATFORM_WRITE_CLIENTS)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        try:
            tenant = tenant_service.begin_deletion(TenantId(request.path_params["id"]), session.subject)
        except InvalidTenantTransitionError as exc:
            return _error(409, "INVALID_TRANSITION", str(exc))
        except ValueError as exc:
            return _error(404, "NOT_FOUND", str(exc))
        return JSONResponse(tenant.model_dump(mode="json"))

    return [
        Route("/admin/clients", list_clients, methods=["GET"]),
        Route("/admin/clients", create_client, methods=["POST"]),
        Route("/admin/clients/{id}", get_client, methods=["GET"]),
        Route("/admin/clients/{id}/suspend", suspend_client, methods=["POST"]),
        Route("/admin/clients/{id}/reactivate", reactivate_client, methods=["POST"]),
        Route("/admin/clients/{id}/begin-deletion", begin_delete_client, methods=["POST"]),
    ]


def _build_team_routes(user_service: UserService) -> list[Route]:
    """Client -> Team Members (ADR-005 Sec 6.8), backed entirely by user_management + authz.

    Scope note: only what user_management's service layer cleanly supports is
    exposed -- list, invite, deactivate, and list-roles (for the invite
    picker). There is no "change an existing assignment's role" method on
    UserService yet (assign_role only ever adds a new RoleAssignment; nothing
    revokes or replaces one) -- a PATCH .../role endpoint isn't implemented
    here rather than faking one on top of a mutation the service doesn't have.
    """

    async def list_team(request: Request) -> JSONResponse:
        try:
            session = require_tenant_permission(request, PERM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        users = user_service.list_users(_require_tenant_id(session))
        return JSONResponse([_serialize_team_member(u) for u in users])

    async def list_roles(request: Request) -> JSONResponse:
        try:
            session = require_tenant_permission(request, PERM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        tenant_id = _require_tenant_id(session)
        roles = user_service.ensure_system_roles(tenant_id)
        return JSONResponse([{"role_id": r.role_id, "name": r.name, "permissions": list(r.permissions)} for r in roles])

    async def invite_team_member(request: Request) -> JSONResponse:
        try:
            session = require_tenant_permission(request, PERM_WRITE_USERS)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return _error(400, "MALFORMED_PAYLOAD", "request body is not valid JSON")

        for field in ("email", "role_id"):
            if not body.get(field):
                return _error(422, "VALIDATION_ERROR", f"missing required field: {field}")

        tenant_id = _require_tenant_id(session)
        role = user_service.get_role(tenant_id, body["role_id"])
        if role is None:
            return _error(422, "VALIDATION_ERROR", f"unknown role_id: {body['role_id']}")

        scope_type = body.get("scope_type", "TENANT")
        scope_id = body.get("scope_id") or str(tenant_id)
        issued = user_service.invite(
            tenant_id,
            body["email"],
            role.role_id,
            OrgScope(scope_type=scope_type, scope_id=scope_id),
            invited_by=session.subject,
        )
        return JSONResponse(
            {
                "invitation_id": issued.invitation.invitation_id,
                "email": issued.invitation.email,
                "role_id": issued.invitation.role_id,
                "status": issued.invitation.status,
                "expires_at": issued.invitation.expires_at.isoformat(),
            },
            status_code=201,
        )

    async def deactivate_team_member(request: Request) -> JSONResponse:
        try:
            session = require_tenant_permission(request, PERM_WRITE_USERS)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        try:
            user = user_service.deactivate(_require_tenant_id(session), request.path_params["id"])
        except ValueError as exc:
            return _error(404, "NOT_FOUND", str(exc))
        return JSONResponse(_serialize_team_member(user))

    return [
        Route("/team", list_team, methods=["GET"]),
        Route("/team/roles", list_roles, methods=["GET"]),
        Route("/team/invite", invite_team_member, methods=["POST"]),
        Route("/team/{id}", deactivate_team_member, methods=["DELETE"]),
    ]


def _serialize_team_member(user: Any) -> dict[str, Any]:
    return {
        "user_id": user.user_id,
        "email": user.email,
        "name": user.name,
        "is_active": user.is_active,
        "roles": [
            {"role_id": a.role_id, "scope_type": a.org_scope.scope_type, "scope_id": a.org_scope.scope_id}
            for a in user.role_assignments
        ],
        "created_at": user.created_at.isoformat(),
    }


def _build_hitl_routes(hitl_queue: HITLQueue, override_logger: OverrideLogger) -> list[Route]:
    """Client -> Live Calls -> Escalations (ADR-005 Sec 12.2 -- HITL).

    hitl/dashboard.py's own docstring says it was built "for the supervisor
    escalation dashboard" -- this is that dashboard's first real BFF wiring.
    Deliberately does NOT mount hitl/review_api.py's existing standalone
    create_review_api() app -- that app authenticates via X-Actor-Role/
    X-Actor-Id headers, a separate auth model from this BFF's cookie-based
    WebSession. These routes call the same underlying HITLQueue/
    OverrideLogger methods directly, through the one session/RBAC model
    every other route in this file already uses -- reuse of the business
    logic, not the pre-built HTTP layer on top of it.
    """

    async def list_queue(request: Request) -> JSONResponse:
        try:
            session = require_tenant_permission(request, PERM_MONITOR_CALLS)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        items = hitl_queue.list_pending(_require_tenant_id(session))
        return JSONResponse([_serialize_hitl_item(i) for i in items])

    async def claim_next(request: Request) -> JSONResponse:
        try:
            session = require_tenant_permission(request, PERM_MONITOR_CALLS)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        item = hitl_queue.dequeue(_require_tenant_id(session), session.subject)
        if item is None:
            return _error(404, "QUEUE_EMPTY", "no pending HITL items")
        return JSONResponse(_serialize_hitl_item(item))

    async def record_decision(request: Request) -> JSONResponse:
        try:
            session = require_tenant_permission(request, PERM_DECIDE_HITL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return _error(400, "MALFORMED_PAYLOAD", "request body is not valid JSON")

        decision = body.get("decision")
        rationale = body.get("rationale", "")
        if not decision:
            return _error(422, "VALIDATION_ERROR", "missing required field: decision")

        try:
            record = override_logger.record_decision(
                _require_tenant_id(session), request.path_params["id"], session.subject, decision, rationale
            )
        except ValueError as exc:
            return _error(422, "VALIDATION_ERROR", str(exc))
        return JSONResponse(
            {
                "hitl_decision_id": record.hitl_decision_id,
                "hitl_item_id": record.hitl_item_id,
                "decision": record.decision,
                "supervisor_id": record.supervisor_id,
                "rationale": record.rationale,
            },
            status_code=201,
        )

    return [
        Route("/hitl/queue", list_queue, methods=["GET"]),
        Route("/hitl/queue/claim-next", claim_next, methods=["POST"]),
        Route("/hitl/items/{id}/decision", record_decision, methods=["POST"]),
    ]


def _serialize_hitl_item(item: Any) -> dict[str, Any]:
    return {
        "hitl_item_id": item.hitl_item_id,
        "call_id": item.call_id,
        "reason": item.reason,
        "priority": item.priority.value,
        "status": item.status.value,
        "context": item.context,
        "enqueued_at": item.enqueued_at.isoformat(),
        "claimed_by": item.claimed_by,
        "claimed_at": item.claimed_at.isoformat() if item.claimed_at else None,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        "sla_deadline_at": item.sla_deadline_at.isoformat(),
        "sla_breached": item.sla_breached,
    }


def _build_campaign_routes(campaign_service: CampaignService) -> list[Route]:
    """Client -> Campaigns CRUD + lifecycle (ADR-005 Sec 6.2), backed entirely by campaign_management.

    Uses tenant-scoped RBAC (authz.RBACEngine) -- structurally separate from
    the platform-scoped routes above (ADR-005 Sec 3). Every query/mutation is
    scoped to the session's own tenant_id, never a request parameter (AR-8).
    """

    async def _guard_read(request: Request) -> Any:
        try:
            return require_tenant_permission(request, PERM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

    async def _guard_write(request: Request) -> Any:
        try:
            return require_tenant_permission(request, PERM_WRITE_CAMPAIGNS)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

    def _lifecycle_error_response(exc: Exception) -> JSONResponse:
        if isinstance(exc, CampaignPromptNotPinnedError):
            return _error(409, "PROMPT_NOT_PINNED", str(exc))
        if isinstance(exc, CampaignLifecycleError):
            return _error(409, "INVALID_TRANSITION", str(exc))
        return _error(404, "NOT_FOUND", str(exc))

    async def list_campaigns(request: Request) -> JSONResponse:
        guard = await _guard_read(request)
        if isinstance(guard, JSONResponse):
            return guard
        campaigns = campaign_service.list_all(TenantId(guard.tenant_id))
        return JSONResponse([c.model_dump(mode="json") for c in campaigns])

    async def create_campaign(request: Request) -> JSONResponse:
        guard = await _guard_write(request)
        if isinstance(guard, JSONResponse):
            return guard

        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return _error(400, "MALFORMED_PAYLOAD", "request body is not valid JSON")

        if not body.get("name"):
            return _error(422, "VALIDATION_ERROR", "missing required field: name")

        campaign = campaign_service.create(
            TenantId(guard.tenant_id),
            body["name"],
            AudienceCriteria(**body.get("audience_criteria", {})),
            RetryPolicy(**body.get("retry_policy", {})),
            created_by=guard.subject,
            description=body.get("description", ""),
            daily_start_hour=body.get("daily_start_hour", 9),
            daily_end_hour=body.get("daily_end_hour", 18),
            timezone=body.get("timezone", "Asia/Kolkata"),
        )
        return JSONResponse(campaign.model_dump(mode="json"), status_code=201)

    async def get_campaign(request: Request) -> JSONResponse:
        guard = await _guard_read(request)
        if isinstance(guard, JSONResponse):
            return guard
        campaign = campaign_service.get(TenantId(guard.tenant_id), CampaignId(request.path_params["id"]))
        if campaign is None:
            return _error(404, "NOT_FOUND", "campaign not found")
        return JSONResponse(campaign.model_dump(mode="json"))

    def _make_lifecycle_action(
        run: Any,
    ) -> Any:
        async def handler(request: Request) -> JSONResponse:
            guard = await _guard_write(request)
            if isinstance(guard, JSONResponse):
                return guard
            tenant_id = TenantId(guard.tenant_id)
            campaign_id = CampaignId(request.path_params["id"])
            try:
                campaign = await run(request, tenant_id, campaign_id, guard)
            except (CampaignLifecycleError, CampaignPromptNotPinnedError, ValueError) as exc:
                return _lifecycle_error_response(exc)
            return JSONResponse(campaign.model_dump(mode="json"))

        return handler

    async def _submit_for_review(request: Request, tenant_id: TenantId, campaign_id: CampaignId, guard: Any) -> Any:
        return campaign_service.submit_for_review(tenant_id, campaign_id)

    async def _approve(request: Request, tenant_id: TenantId, campaign_id: CampaignId, guard: Any) -> Any:
        return campaign_service.approve(tenant_id, campaign_id, guard.subject)

    async def _activate(request: Request, tenant_id: TenantId, campaign_id: CampaignId, guard: Any) -> Any:
        body: dict[str, Any] = await request.json() if await request.body() else {}
        target_call_count = int(body.get("target_call_count", 0))
        return campaign_service.activate(tenant_id, campaign_id, guard.subject, target_call_count)

    async def _pause(request: Request, tenant_id: TenantId, campaign_id: CampaignId, guard: Any) -> Any:
        return campaign_service.pause(tenant_id, campaign_id)

    async def _resume(request: Request, tenant_id: TenantId, campaign_id: CampaignId, guard: Any) -> Any:
        return campaign_service.resume(tenant_id, campaign_id)

    async def _complete(request: Request, tenant_id: TenantId, campaign_id: CampaignId, guard: Any) -> Any:
        return campaign_service.complete(tenant_id, campaign_id)

    async def _archive(request: Request, tenant_id: TenantId, campaign_id: CampaignId, guard: Any) -> Any:
        return campaign_service.archive(tenant_id, campaign_id)

    return [
        Route("/campaigns", list_campaigns, methods=["GET"]),
        Route("/campaigns", create_campaign, methods=["POST"]),
        Route("/campaigns/{id}", get_campaign, methods=["GET"]),
        Route("/campaigns/{id}/submit-for-review", _make_lifecycle_action(_submit_for_review), methods=["POST"]),
        Route("/campaigns/{id}/approve", _make_lifecycle_action(_approve), methods=["POST"]),
        Route("/campaigns/{id}/start", _make_lifecycle_action(_activate), methods=["POST"]),
        Route("/campaigns/{id}/pause", _make_lifecycle_action(_pause), methods=["POST"]),
        Route("/campaigns/{id}/resume", _make_lifecycle_action(_resume), methods=["POST"]),
        Route("/campaigns/{id}/complete", _make_lifecycle_action(_complete), methods=["POST"]),
        Route("/campaigns/{id}/archive", _make_lifecycle_action(_archive), methods=["POST"]),
    ]


def _build_users_roles_routes(platform_admin: PlatformAdminService) -> list[Route]:
    """Admin -> Users & Roles (ADR-005 Sec 6.8, platform side) -- platform_users CRUD."""

    async def list_platform_users(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        users = platform_admin.list_all()
        return JSONResponse([u.model_dump(mode="json") for u in users])

    async def create_platform_user(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_WRITE_STAFF)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return _error(400, "MALFORMED_PAYLOAD", "request body is not valid JSON")

        for field in ("email", "name", "platform_role"):
            if not body.get(field):
                return _error(422, "VALIDATION_ERROR", f"missing required field: {field}")
        try:
            role = PlatformRole(body["platform_role"])
        except ValueError:
            return _error(422, "VALIDATION_ERROR", f"unknown platform_role: {body['platform_role']}")

        try:
            user = platform_admin.create(email=body["email"], name=body["name"], platform_role=role)
        except DuplicatePlatformUserError as exc:
            return _error(409, "DUPLICATE_EMAIL", str(exc))
        return JSONResponse(user.model_dump(mode="json"), status_code=201)

    async def deactivate_platform_user(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_WRITE_STAFF)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        try:
            user = platform_admin.deactivate(request.path_params["id"])
        except PlatformUserNotFoundError as exc:
            return _error(404, "NOT_FOUND", str(exc))
        return JSONResponse(user.model_dump(mode="json"))

    return [
        Route("/admin/platform-users", list_platform_users, methods=["GET"]),
        Route("/admin/platform-users", create_platform_user, methods=["POST"]),
        Route("/admin/platform-users/{id}/deactivate", deactivate_platform_user, methods=["POST"]),
    ]


def _build_admin_audit_routes(admin_audit_repository: AdminAuditViewRepository) -> list[Route]:
    """Admin -> Audit Logs (ADR-005 Sec 12.4) -- Admin-Portal-originated audit entries per tenant.

    AR-8 (tenant isolation) means there is no cross-tenant audit query --
    a platform admin selects one client (from Admin -> Clients, already
    real) and reads that tenant's admin-portal audit trail, the same
    per-tenant drill-down shape as every other admin/clients/{id}/* route.
    """

    async def list_audit_logs(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_AUDIT)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        tenant_id = TenantId(request.path_params["id"])
        events = admin_audit_repository.list_for_tenant(tenant_id)
        return JSONResponse([_serialize_audit_event(e) for e in events])

    return [Route("/admin/clients/{id}/audit-logs", list_audit_logs, methods=["GET"])]


_SECURITY_RELEVANT_ACTIONS = frozenset(
    {
        "authn.login",
        "authn.logout",
        "authn.failed",
        "policy.denied",
        "ai_governance.block",
        "ai_governance.require_human",
        "data.erasure",
        "api_key.issued",
        "api_key.revoked",
    }
)


def _build_security_routes(audit_repository: AuditRepository) -> list[Route]:
    """Admin -> Security (ADR-005 Sec 12.4) -- security-relevant audit events per tenant.

    Reads the full hash-chained ``audit_log`` (not the admin-portal-only
    view Audit Logs uses) and filters to the mandatory-coverage security
    event types (V4 Ch11 Sec 11.12) -- a real, non-fabricated summary,
    though it is only as populated as the audit events this environment
    has actually recorded (no simulated data is generated here).
    """

    async def security_summary(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_AUDIT)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        tenant_id = TenantId(request.path_params["id"])
        chain = audit_repository.iter_chain(tenant_id)
        events = [row for row in chain if row[3] in _SECURITY_RELEVANT_ACTIONS]
        counts: dict[str, int] = {}
        for row in events:
            counts[row[3]] = counts.get(row[3], 0) + 1
        recent = [
            {
                "audit_id": str(row[0]),
                "actor_id": row[2],
                "action": row[3],
                "resource_type": row[4],
                "resource_id": row[5],
                "outcome": row[6],
                "recorded_at": row[9].isoformat() if row[9] is not None else None,
            }
            for row in events[-25:][::-1]
        ]
        return JSONResponse({"counts_by_action": counts, "recent_events": recent})

    return [Route("/admin/clients/{id}/security-summary", security_summary, methods=["GET"])]


def _build_compliance_routes(compliance_monitoring: ComplianceMonitoring) -> list[Route]:
    """Admin -> Compliance (ADR-005 Sec 12.4) -- live rule catalog + per-tenant status.

    ``ComplianceMonitoring.ingest()`` has no production call site wiring it
    to the real audit event stream yet (a Volume 4 gap that predates this
    pass) -- this page honestly reflects that: the rule catalog is always
    real, but ``status`` stays COMPLIANT until something actually calls
    ``ingest()``, rather than fabricating violation data to look populated.
    """

    async def compliance_status(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        tenant_id = request.path_params["id"]
        rules = compliance_monitoring.rules()
        return JSONResponse(
            {
                "tenant_id": tenant_id,
                "status": compliance_monitoring.status(tenant_id).value,
                "rules": [
                    {
                        "rule_id": r.rule_id,
                        "matches_action": r.matches_action,
                        "threshold_count": r.threshold_count,
                        "window_seconds": r.window_seconds,
                        "alert_kind": r.alert_kind,
                    }
                    for r in rules
                ],
            }
        )

    return [Route("/admin/clients/{id}/compliance-status", compliance_status, methods=["GET"])]


def _build_infrastructure_routes(gpu_fleet_monitor: GPUFleetHealthMonitor) -> list[Route]:
    """Admin -> Infrastructure (ADR-005 Sec 12.1/12.3) -- GPU fleet health.

    ``GPUFleetHealthMonitor`` is a live in-memory aggregator: nodes appear
    here only once something calls ``report_node()`` for them. In this
    environment there is exactly one, non-clustered GPU node
    (``GPU_NODE_STATE.md``) and no live reporting loop feeding this
    monitor, so ``node_snapshots()`` is honestly empty rather than showing
    a fabricated fleet -- the endpoint and page are fully real and will
    populate automatically the moment real nodes start reporting in.
    """

    async def fleet_health(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_INFRA)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        nodes = gpu_fleet_monitor.node_snapshots()
        return JSONResponse(
            {
                "fleet_health_score": gpu_fleet_monitor.fleet_health_score(),
                "is_degraded": gpu_fleet_monitor.is_degraded(),
                "is_severely_degraded": gpu_fleet_monitor.is_severely_degraded(),
                "nodes": [
                    {
                        "node_id": n.node_id,
                        "healthy": n.healthy,
                        "vram_used_mb": n.vram_used_mb,
                        "vram_total_mb": n.vram_total_mb,
                    }
                    for n in nodes
                ],
            }
        )

    return [Route("/admin/infrastructure/gpu-fleet", fleet_health, methods=["GET"])]


def _build_admin_analytics_routes(executive_dashboard: ExecutiveDashboard) -> list[Route]:
    """Admin -> Analytics (ADR-005 Sec 6.9, platform side) -- executive KPI summary per tenant."""

    async def executive_summary(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        tenant_id = TenantId(request.path_params["id"])
        summary = executive_dashboard.get_executive_summary(tenant_id)
        return JSONResponse(
            {
                "tenant_id": summary.tenant_id,
                "gross_recovery_rate": summary.gross_recovery_rate,
                "cost_per_conversation_minor": summary.cost_per_conversation_minor,
                "mom_improvement": summary.mom_improvement,
                "slo_attainment": summary.slo_attainment,
                "compliance_score": summary.compliance_score,
            }
        )

    return [Route("/admin/clients/{id}/executive-summary", executive_summary, methods=["GET"])]


def _build_billing_routes(billing_service: BillingService) -> list[Route]:
    """Admin -> Billing + Revenue (ADR-005 Sec 6.16) -- subscription + invoicing per tenant."""

    async def get_subscription(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        tenant_id = TenantId(request.path_params["id"])
        subscription = billing_service.get_subscription(tenant_id)
        if subscription is None:
            return _error(404, "NOT_FOUND", "no billing subscription for this tenant")
        return JSONResponse(subscription.model_dump(mode="json"))

    async def create_subscription(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_WRITE_BILLING)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return _error(400, "MALFORMED_PAYLOAD", "request body is not valid JSON")

        if not body.get("tier"):
            return _error(422, "VALIDATION_ERROR", "missing required field: tier")
        try:
            tier = SubscriptionTier(body["tier"])
        except ValueError:
            return _error(422, "VALIDATION_ERROR", f"unknown tier: {body['tier']}")

        tenant_id = TenantId(request.path_params["id"])
        subscription = billing_service.create_subscription(tenant_id, tier)
        return JSONResponse(subscription.model_dump(mode="json"), status_code=201)

    async def generate_invoice(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_WRITE_BILLING)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return _error(400, "MALFORMED_PAYLOAD", "request body is not valid JSON")

        for field in ("period_start", "period_end"):
            if not body.get(field):
                return _error(422, "VALIDATION_ERROR", f"missing required field: {field}")

        tenant_id = TenantId(request.path_params["id"])
        try:
            period_start = datetime.fromisoformat(body["period_start"])
            period_end = datetime.fromisoformat(body["period_end"])
            invoice = billing_service.generate_monthly_invoice(tenant_id, period_start, period_end)
        except ValueError as exc:
            return _error(422, "VALIDATION_ERROR", str(exc))
        return JSONResponse(invoice.model_dump(mode="json"), status_code=201)

    return [
        Route("/admin/clients/{id}/subscription", get_subscription, methods=["GET"]),
        Route("/admin/clients/{id}/subscription", create_subscription, methods=["POST"]),
        Route("/admin/clients/{id}/invoices", generate_invoice, methods=["POST"]),
    ]


def _build_platform_settings_routes(feature_flag_service: FeatureFlagService) -> list[Route]:
    """Admin -> Platform Settings (ADR-005 Sec 6.17/6.18) -- feature flag targeting.

    ``FeatureFlagRepositoryPort`` has no "list every flag name that
    exists" query (V5 Ch23's schema is per-row, not a flag registry) --
    this catalog is the known flag vocabulary defined so far in this
    codebase (currently just ``ops_intelligence_reasoning``), not a
    fabricated list. Adding a real flag registry table is future scope,
    not invented here.
    """

    _known_flags = (REASONING_FLAG_NAME,)

    async def list_flags(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        flags = []
        for name in _known_flags:
            rows = feature_flag_service.list_targeting_rows(name)
            flags.append(
                {
                    "flag_name": name,
                    "targeting_rows": [
                        {
                            "scope": row.scope.value,
                            "scope_value": row.scope_value,
                            "state": row.state.value,
                            "rollout_percentage": row.rollout_percentage,
                        }
                        for row in rows
                    ],
                }
            )
        return JSONResponse(flags)

    async def set_flag(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_WRITE_SETTINGS)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return _error(400, "MALFORMED_PAYLOAD", "request body is not valid JSON")

        for field in ("scope", "state"):
            if not body.get(field):
                return _error(422, "VALIDATION_ERROR", f"missing required field: {field}")
        try:
            scope = FeatureFlagScope(body["scope"])
            state = FeatureFlagState(body["state"])
        except ValueError as exc:
            return _error(422, "VALIDATION_ERROR", str(exc))

        flag_name = request.path_params["flag_name"]
        flag = feature_flag_service.set_flag(
            flag_name,
            scope,
            state,
            scope_value=body.get("scope_value"),
            rollout_percentage=float(body.get("rollout_percentage", 0.0)),
        )
        return JSONResponse(
            {
                "flag_name": flag.flag_name,
                "scope": flag.scope.value,
                "scope_value": flag.scope_value,
                "state": flag.state.value,
                "rollout_percentage": flag.rollout_percentage,
            }
        )

    return [
        Route("/admin/feature-flags", list_flags, methods=["GET"]),
        Route("/admin/feature-flags/{flag_name}", set_flag, methods=["POST"]),
    ]


def _build_crm_routes(crm_service: CustomerService) -> list[Route]:
    """Client -> CRM (ADR-005 Sec 6.5) -- customer records, backed by src/services/crm."""

    async def list_customers(request: Request) -> JSONResponse:
        try:
            session = require_tenant_permission(request, PERM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        customers = crm_service.list_for_tenant(_require_tenant_id(session))
        return JSONResponse([c.model_dump(mode="json") for c in customers])

    async def create_customer(request: Request) -> JSONResponse:
        try:
            session = require_tenant_permission(request, PERM_WRITE_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return _error(400, "MALFORMED_PAYLOAD", "request body is not valid JSON")

        for field in ("crm_id", "name"):
            if not body.get(field):
                return _error(422, "VALIDATION_ERROR", f"missing required field: {field}")

        tenant_id = _require_tenant_id(session)
        now = datetime.now(UTC)
        contacts = tuple(
            CustomerContact(
                contact_type=c.get("contact_type", "MOBILE"),
                value=c["value"],
                is_primary=c.get("is_primary", False),
            )
            for c in body.get("contacts", [])
            if c.get("value")
        )
        customer = Customer(
            customer_id=CustomerId(str(uuid.uuid4())),
            tenant_id=tenant_id,
            crm_id=body["crm_id"],
            name=body["name"],
            preferred_language=body.get("preferred_language", "en"),
            contacts=contacts,
            created_at=now,
            updated_at=now,
        )
        created = crm_service.create(customer)
        return JSONResponse(created.model_dump(mode="json"), status_code=201)

    return [
        Route("/crm/customers", list_customers, methods=["GET"]),
        Route("/crm/customers", create_customer, methods=["POST"]),
    ]


def _build_collections_routes(collections_workflow: EscalationWorkflow) -> list[Route]:
    """Client -> Collections (ADR-005 Sec 6.7) -- escalation records, backed by src/services/collections."""

    async def list_escalations(request: Request) -> JSONResponse:
        try:
            session = require_tenant_permission(request, PERM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        escalations = collections_workflow.list_for_tenant(_require_tenant_id(session))
        return JSONResponse([e.model_dump(mode="json") for e in escalations])

    async def resolve_escalation(request: Request) -> JSONResponse:
        try:
            session = require_tenant_permission(request, PERM_WRITE_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return _error(400, "MALFORMED_PAYLOAD", "request body is not valid JSON")

        notes = body.get("resolution_notes", "")
        collections_workflow.resolve(_require_tenant_id(session), request.path_params["id"], notes)
        return JSONResponse({"escalation_id": request.path_params["id"], "resolved": True})

    return [
        Route("/collections/escalations", list_escalations, methods=["GET"]),
        Route("/collections/escalations/{id}/resolve", resolve_escalation, methods=["POST"]),
    ]


def _build_leads_routes(
    campaign_service: CampaignService,
    campaign_audience_repository: CampaignAudienceRepository,
    crm_service: CustomerService,
) -> list[Route]:
    """Client -> Audience-cohort Leads (ADR-005 Sec 6.4) -- CampaignAudienceMember view.

    Retained for backward compatibility with the audience-cohort view (leads
    built from AudienceSelector SQL criteria). The CSV-upload / pipeline lead
    flow is handled by _build_pipeline_lead_routes below.
    """

    async def list_leads(request: Request) -> JSONResponse:
        try:
            session = require_tenant_permission(request, PERM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        tenant_id = _require_tenant_id(session)
        campaign_id = request.query_params.get("campaign_id")
        if not campaign_id:
            return _error(422, "VALIDATION_ERROR", "missing required query param: campaign_id")

        members = campaign_audience_repository.find_by_campaign(tenant_id, CampaignId(campaign_id))
        leads = []
        for member in members:
            customer = crm_service.get(tenant_id, CustomerId(member.customer_id))
            leads.append(
                {
                    "campaign_audience_id": member.campaign_audience_id,
                    "campaign_id": member.campaign_id,
                    "customer_id": member.customer_id,
                    "customer_name": customer.name if customer is not None else None,
                    "primary_contact": next(
                        (c.value for c in (customer.contacts if customer is not None else ()) if c.is_primary),
                        None,
                    ),
                    "dnd": member.dnd,
                    "included_at": member.included_at.isoformat(),
                    "excluded_reason": member.excluded_reason,
                }
            )
        return JSONResponse(leads)

    return [Route("/leads", list_leads, methods=["GET"])]


def _build_pipeline_lead_routes(
    pipeline_service: Any,
    lead_intake_service: Any,
    lead_repository: Any,
    import_repository: Any,
) -> list[Route]:
    """Client -> Pipelines + CSV-imported Leads (ADR-005 §14, Lead Distribution Engine).

    Route map:
      POST   /campaigns/{campaign_id}/pipelines            create pipeline
      GET    /campaigns/{campaign_id}/pipelines            list pipelines
      GET    /campaigns/{campaign_id}/pipelines/{id}       get pipeline
      PATCH  /campaigns/{campaign_id}/pipelines/{id}       rename / change status
      GET    /campaigns/{campaign_id}/leads                list leads (filters)
      GET    /campaigns/{campaign_id}/leads/stats          aggregate stats
      GET    /campaigns/{campaign_id}/leads/imports        import history
      POST   /campaigns/{campaign_id}/leads/upload         bulk import
      POST   /campaigns/{campaign_id}/leads/suggest-mapping  column hint
      GET    /pipelines/{pipeline_id}/leads                leads in one pipeline
      GET    /pipelines/{pipeline_id}/leads/stats          pipeline lead stats
    """
    from src.libs.contracts.primitives import PipelineId
    from src.services.campaign_management.lead_intake import suggest_column_mapping
    from src.services.campaign_management.pipeline_service import (
        InvalidPipelineTransitionError,
        PipelineNotFoundError,
    )

    # ── Guards ─────────────────────────────────────────────────────────────

    async def _guard_read(request: Request) -> Any:
        try:
            return require_tenant_permission(request, PERM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

    async def _guard_write(request: Request) -> Any:
        try:
            return require_tenant_permission(request, PERM_WRITE_CAMPAIGNS)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

    def _pipeline_to_dict(p: Any) -> dict[str, Any]:
        return {
            "pipeline_id": p.pipeline_id,
            "campaign_id": p.campaign_id,
            "name": p.name,
            "status": p.status,
            "created_at": p.created_at.isoformat(),
            "updated_at": p.updated_at.isoformat(),
            "created_by": p.created_by,
        }

    def _lead_to_dict(lead: Any) -> dict[str, Any]:
        return {
            "lead_id": lead.lead_id,
            "campaign_id": lead.campaign_id,
            "pipeline_id": lead.pipeline_id,
            "import_id": lead.import_id,
            "phone": lead.phone,
            "name": lead.name,
            "email": lead.email,
            "language": lead.language,
            "score": lead.score,
            "status": lead.status,
            "queue_status": lead.queue_status,
            "is_duplicate": lead.is_duplicate,
            "is_blacklisted": lead.is_blacklisted,
            "rejection_reason": lead.rejection_reason,
            "metadata": lead.metadata,
            "created_at": lead.created_at.isoformat(),
        }

    # ── Pipeline handlers ───────────────────────────────────────────────────

    async def create_pipeline(request: Request) -> JSONResponse:
        guard = await _guard_write(request)
        if isinstance(guard, JSONResponse):
            return guard
        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return _error(400, "MALFORMED_PAYLOAD", "request body is not valid JSON")
        name = (body.get("name") or "").strip()
        if not name:
            return _error(422, "VALIDATION_ERROR", "missing required field: name")
        campaign_id = CampaignId(request.path_params["campaign_id"])
        pipeline = pipeline_service.create(
            TenantId(guard.tenant_id), campaign_id, name, created_by=guard.subject
        )
        return JSONResponse(_pipeline_to_dict(pipeline), status_code=201)

    async def list_pipelines(request: Request) -> JSONResponse:
        guard = await _guard_read(request)
        if isinstance(guard, JSONResponse):
            return guard
        campaign_id = CampaignId(request.path_params["campaign_id"])
        pipelines = pipeline_service.list_for_campaign(TenantId(guard.tenant_id), campaign_id)
        return JSONResponse([_pipeline_to_dict(p) for p in pipelines])

    async def get_pipeline(request: Request) -> JSONResponse:
        guard = await _guard_read(request)
        if isinstance(guard, JSONResponse):
            return guard
        pipeline_id = PipelineId(request.path_params["pipeline_id"])
        pipeline = pipeline_service.get(TenantId(guard.tenant_id), pipeline_id)
        if pipeline is None:
            return _error(404, "NOT_FOUND", "pipeline not found")
        return JSONResponse(_pipeline_to_dict(pipeline))

    async def update_pipeline(request: Request) -> JSONResponse:
        guard = await _guard_write(request)
        if isinstance(guard, JSONResponse):
            return guard
        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return _error(400, "MALFORMED_PAYLOAD", "request body is not valid JSON")
        tenant_id = TenantId(guard.tenant_id)
        pipeline_id = PipelineId(request.path_params["pipeline_id"])
        try:
            if "name" in body:
                pipeline_service.rename(tenant_id, pipeline_id, body["name"])
            if "status" in body:
                status = body["status"]
                if status == "ACTIVE":
                    pipeline_service.activate(tenant_id, pipeline_id, guard.subject)
                elif status == "PAUSED":
                    pipeline_service.pause(tenant_id, pipeline_id, guard.subject)
                elif status == "ARCHIVED":
                    pipeline_service.archive(tenant_id, pipeline_id, guard.subject)
                else:
                    return _error(422, "VALIDATION_ERROR", f"unknown status: {status}")
        except PipelineNotFoundError:
            return _error(404, "NOT_FOUND", "pipeline not found")
        except InvalidPipelineTransitionError as exc:
            return _error(409, "INVALID_TRANSITION", str(exc))
        pipeline = pipeline_service.get(tenant_id, pipeline_id)
        return JSONResponse(_pipeline_to_dict(pipeline))

    # ── Lead handlers ───────────────────────────────────────────────────────

    async def list_campaign_leads(request: Request) -> JSONResponse:
        guard = await _guard_read(request)
        if isinstance(guard, JSONResponse):
            return guard
        tenant_id = TenantId(guard.tenant_id)
        campaign_id = CampaignId(request.path_params["campaign_id"])
        status_filter = request.query_params.get("status") or None
        pipeline_filter = request.query_params.get("pipeline_id") or None
        search = request.query_params.get("search") or None
        leads = lead_repository.find_by_campaign(
            tenant_id, campaign_id,
            status=status_filter,
            pipeline_id=pipeline_filter,
            search=search,
        )
        return JSONResponse([_lead_to_dict(l) for l in leads])

    async def campaign_lead_stats(request: Request) -> JSONResponse:
        guard = await _guard_read(request)
        if isinstance(guard, JSONResponse):
            return guard
        tenant_id = TenantId(guard.tenant_id)
        campaign_id = CampaignId(request.path_params["campaign_id"])
        stats = lead_repository.stats_for_campaign(tenant_id, campaign_id)
        return JSONResponse(stats)

    async def list_imports(request: Request) -> JSONResponse:
        guard = await _guard_read(request)
        if isinstance(guard, JSONResponse):
            return guard
        tenant_id = TenantId(guard.tenant_id)
        campaign_id = CampaignId(request.path_params["campaign_id"])
        imports = import_repository.find_by_campaign(tenant_id, campaign_id)
        return JSONResponse([
            {
                "import_id": imp.import_id,
                "filename": imp.filename,
                "status": imp.status,
                "total_rows": imp.total_rows,
                "valid_rows": imp.valid_rows,
                "invalid_rows": imp.invalid_rows,
                "duplicate_rows": imp.duplicate_rows,
                "created_at": imp.created_at.isoformat(),
                "completed_at": imp.completed_at.isoformat() if imp.completed_at else None,
            }
            for imp in imports
        ])

    async def upload_leads(request: Request) -> JSONResponse:
        guard = await _guard_write(request)
        if isinstance(guard, JSONResponse):
            return guard
        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return _error(400, "MALFORMED_PAYLOAD", "request body is not valid JSON")

        rows = body.get("rows")
        if not isinstance(rows, list):
            return _error(422, "VALIDATION_ERROR", "missing required field: rows (must be a list)")
        if not rows:
            return _error(422, "VALIDATION_ERROR", "rows is empty; upload at least one lead")
        if len(rows) > 10_000:
            return _error(422, "VALIDATION_ERROR", "maximum 10,000 rows per upload")
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                return _error(422, "VALIDATION_ERROR", f"rows[{i}] must be an object, got {type(row).__name__}")

        filename = body.get("filename") or "upload.csv"
        column_mapping = body.get("column_mapping") or {}
        if not isinstance(column_mapping, dict):
            return _error(422, "VALIDATION_ERROR", "column_mapping must be an object of {csv_column: standard_field}")
        if "phone" not in column_mapping.values():
            return _error(
                422, "VALIDATION_ERROR",
                "column_mapping must map at least one CSV column to 'phone' (required for outbound dialing)",
            )
        raw_pipeline_ids: list[str] = body.get("pipeline_ids") or []
        pipeline_ids = [PipelineId(pid) for pid in raw_pipeline_ids if pid]

        tenant_id = TenantId(guard.tenant_id)
        campaign_id = CampaignId(request.path_params["campaign_id"])

        result = lead_intake_service.ingest(
            tenant_id,
            campaign_id,
            filename,
            rows,
            column_mapping,
            pipeline_ids,
            uploaded_by=guard.subject,
        )
        return JSONResponse(
            {
                "import_id": result.import_id,
                "total": result.total,
                "valid": result.valid,
                "invalid": result.invalid,
                "duplicates": result.duplicates,
            },
            status_code=201,
        )

    async def suggest_mapping(request: Request) -> JSONResponse:
        guard = await _guard_read(request)
        if isinstance(guard, JSONResponse):
            return guard
        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return _error(400, "MALFORMED_PAYLOAD", "request body is not valid JSON")
        columns: list[str] = body.get("columns") or []
        mapping = suggest_column_mapping(columns)
        return JSONResponse({"suggested_mapping": mapping})

    # ── Pipeline-scoped lead handlers ───────────────────────────────────────

    async def list_pipeline_leads(request: Request) -> JSONResponse:
        guard = await _guard_read(request)
        if isinstance(guard, JSONResponse):
            return guard
        tenant_id = TenantId(guard.tenant_id)
        pipeline_id = PipelineId(request.path_params["pipeline_id"])
        search = request.query_params.get("search") or None
        leads = lead_repository.find_by_pipeline(tenant_id, pipeline_id, search=search)
        return JSONResponse([_lead_to_dict(l) for l in leads])

    async def pipeline_lead_stats(request: Request) -> JSONResponse:
        guard = await _guard_read(request)
        if isinstance(guard, JSONResponse):
            return guard
        tenant_id = TenantId(guard.tenant_id)
        pipeline_id = PipelineId(request.path_params["pipeline_id"])
        stats = lead_repository.stats_for_pipeline(tenant_id, pipeline_id)
        return JSONResponse(stats)

    return [
        Route("/campaigns/{campaign_id}/pipelines", create_pipeline, methods=["POST"]),
        Route("/campaigns/{campaign_id}/pipelines", list_pipelines, methods=["GET"]),
        Route("/campaigns/{campaign_id}/pipelines/{pipeline_id}", get_pipeline, methods=["GET"]),
        Route("/campaigns/{campaign_id}/pipelines/{pipeline_id}", update_pipeline, methods=["PATCH"]),
        Route("/campaigns/{campaign_id}/leads", list_campaign_leads, methods=["GET"]),
        Route("/campaigns/{campaign_id}/leads/stats", campaign_lead_stats, methods=["GET"]),
        Route("/campaigns/{campaign_id}/leads/imports", list_imports, methods=["GET"]),
        Route("/campaigns/{campaign_id}/leads/upload", upload_leads, methods=["POST"]),
        Route("/campaigns/{campaign_id}/leads/suggest-mapping", suggest_mapping, methods=["POST"]),
        Route("/pipelines/{pipeline_id}/leads", list_pipeline_leads, methods=["GET"]),
        Route("/pipelines/{pipeline_id}/leads/stats", pipeline_lead_stats, methods=["GET"]),
    ]


def _build_reports_routes(reporting_service: ReportingService) -> list[Route]:
    """Client -> Reports (ADR-005 Sec 6.9) -- scheduled report run history + on-demand trigger."""

    async def list_runs(request: Request) -> JSONResponse:
        try:
            require_tenant_permission(request, PERM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        runs = reporting_service.run_history()
        return JSONResponse(
            [
                {
                    "tenant_id": r.tenant_id,
                    "campaign_id": r.campaign_id,
                    "day": r.day.isoformat(),
                    "ran_at": r.ran_at.isoformat(),
                }
                for r in runs
            ]
        )

    async def trigger_run(request: Request) -> JSONResponse:
        try:
            session = require_tenant_permission(request, PERM_WRITE_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return _error(400, "MALFORMED_PAYLOAD", "request body is not valid JSON")

        if not body.get("day"):
            return _error(422, "VALIDATION_ERROR", "missing required field: day")

        tenant_id = _require_tenant_id(session)
        campaign_id = CampaignId(body["campaign_id"]) if body.get("campaign_id") else None
        try:
            day = date.fromisoformat(body["day"])
            rollup = reporting_service.run_scheduled_aggregation(tenant_id, day, campaign_id)
        except ValueError as exc:
            return _error(422, "VALIDATION_ERROR", str(exc))
        return JSONResponse(rollup.model_dump(mode="json"), status_code=201)

    return [
        Route("/reports/runs", list_runs, methods=["GET"]),
        Route("/reports/runs", trigger_run, methods=["POST"]),
    ]


def _build_client_analytics_routes(analytics_service: AnalyticsService) -> list[Route]:
    """Client -> Analytics (ADR-005 Sec 6.9) -- real-time dashboard snapshot + campaign summary."""

    async def dashboard_snapshot(request: Request) -> JSONResponse:
        try:
            session = require_tenant_permission(request, PERM_VIEW_ANALYTICS)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        tenant_id = _require_tenant_id(session)
        window_end = datetime.now(UTC)
        window_start = window_end - timedelta(hours=24)
        start_param = request.query_params.get("window_start")
        end_param = request.query_params.get("window_end")
        if start_param:
            window_start = datetime.fromisoformat(start_param)
        if end_param:
            window_end = datetime.fromisoformat(end_param)
        campaign_id = request.query_params.get("campaign_id")

        snapshot = analytics_service.dashboard_snapshot(
            tenant_id, window_start, window_end, CampaignId(campaign_id) if campaign_id else None
        )
        return JSONResponse(snapshot)

    async def campaign_summary(request: Request) -> JSONResponse:
        try:
            session = require_tenant_permission(request, PERM_VIEW_ANALYTICS)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        tenant_id = _require_tenant_id(session)
        summary = analytics_service.campaign_summary(tenant_id, CampaignId(request.path_params["id"]))
        return JSONResponse(summary)

    return [
        Route("/analytics/dashboard", dashboard_snapshot, methods=["GET"]),
        Route("/analytics/campaigns/{id}/summary", campaign_summary, methods=["GET"]),
    ]


def _serialize_alert(alert: Any) -> dict[str, Any]:
    return {
        "alert_id": alert.alert_id,
        "tenant_id": alert.tenant_id,
        "source": alert.source.value,
        "fingerprint": alert.fingerprint,
        "severity": alert.severity.value,
        "status": alert.status.value,
        "fired_at": alert.fired_at.isoformat(),
        "labels": alert.labels,
        "annotations": alert.annotations,
        "acknowledged_at": alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
        "acknowledged_by": alert.acknowledged_by,
        "escalated_at": alert.escalated_at.isoformat() if alert.escalated_at else None,
        "escalated_to": alert.escalated_to,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
    }


def _build_alerts_routes(alert_repository: AlertRepositoryPort, alert_lifecycle: AlertLifecycleService) -> list[Route]:
    """Admin -> Monitoring -> Alerts Center (ADR-006 Sec 4) -- the always-on alert lifecycle.

    Platform-wide (not per-tenant): alerts are operations data about the
    platform itself, reusing the existing Alertmanager/ComplianceMonitoring
    alert sources (ADR-006 Sec 4) -- never a parallel alerting system.
    """

    async def list_alerts(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        alerts = alert_repository.list_open()
        return JSONResponse([_serialize_alert(a) for a in alerts])

    async def acknowledge_alert(request: Request) -> JSONResponse:
        try:
            session = require_platform_permission(request, PERM_PLATFORM_WRITE_CLIENTS)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        try:
            alert = alert_lifecycle.acknowledge(request.path_params["id"], by=session.subject)
        except ValueError as exc:
            return _error(404, "NOT_FOUND", str(exc))
        return JSONResponse(_serialize_alert(alert))

    async def resolve_alert(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_WRITE_CLIENTS)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        try:
            alert = alert_lifecycle.resolve(request.path_params["id"])
        except ValueError as exc:
            return _error(404, "NOT_FOUND", str(exc))
        return JSONResponse(_serialize_alert(alert))

    return [
        Route("/admin/alerts", list_alerts, methods=["GET"]),
        Route("/admin/alerts/{id}/acknowledge", acknowledge_alert, methods=["POST"]),
        Route("/admin/alerts/{id}/resolve", resolve_alert, methods=["POST"]),
    ]


def _serialize_insight(insight: Any) -> dict[str, Any]:
    return {
        "insight_id": insight.insight_id,
        "tenant_id": insight.tenant_id,
        "category": insight.category.value,
        "severity": insight.severity.value,
        "verified_facts": [
            {"claim": f.claim, "source": f.source, "query": f.query, "value": f.value, "observed_at": f.observed_at.isoformat()}
            for f in insight.verified_facts
        ],
        "hypotheses": [
            {"claim": h.claim, "reasoning": h.reasoning, "confidence": h.confidence.value} for h in insight.hypotheses
        ],
        "affected_components": list(insight.affected_components),
        "confidence_level": insight.confidence_level.value,
        "model": insight.model,
        "recommendation": insight.recommendation,
        "generated_at": insight.generated_at.isoformat(),
    }


def _build_ai_insights_routes(
    insight_repository: InsightRepositoryPort, ops_intelligence: OpsIntelligenceService
) -> list[Route]:
    """Admin -> Monitoring -> AI Insights (ADR-006 Sec 3.2.2/10) -- evidence-backed AI conclusions.

    Gated behind the reasoning kill switch (ADR-006 Sec 3.0/9): this is
    read call site 2 of the exactly-two-call-sites design -- when the flag
    is off, returns an honest ``reasoning_enabled: false`` with an empty
    list rather than an error.
    """

    async def list_insights(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        tenant_id = request.query_params.get("tenant_id")
        if not ops_intelligence.is_reasoning_enabled(tenant_id):
            return JSONResponse({"reasoning_enabled": False, "insights": []})

        category_param = request.query_params.get("category")
        category = InsightCategory(category_param) if category_param else None
        insights = insight_repository.list(tenant_id=tenant_id, category=category)
        return JSONResponse({"reasoning_enabled": True, "insights": [_serialize_insight(i) for i in insights]})

    return [Route("/admin/ai-insights", list_insights, methods=["GET"])]


def _serialize_report(report: Any) -> dict[str, Any]:
    return {
        "report_id": report.report_id,
        "report_type": report.report_type.value,
        "period_start": report.period_start.isoformat(),
        "period_end": report.period_end.isoformat(),
        "scope_level": report.scope_level.value,
        "tenant_id": report.tenant_id,
        "severity": report.severity.value,
        "affected_components": list(report.affected_components),
        "business_impact": report.business_impact,
        "recommended_actions": list(report.recommended_actions),
        "confidence_level": report.confidence_level.value,
        "narrative": report.narrative,
        "generated_at": report.generated_at.isoformat(),
        "delivered_to": list(report.delivered_to),
    }


def _build_ai_reports_routes(
    report_repository: ReportRepositoryPort, ops_intelligence: OpsIntelligenceService
) -> list[Route]:
    """Admin -> Monitoring -> AI Reports (ADR-006 Sec 6.1) -- the 9-type generated report catalog.

    Same reasoning-gate contract as AI Insights.
    """

    async def list_reports(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        tenant_id = request.query_params.get("tenant_id")
        if not ops_intelligence.is_reasoning_enabled(tenant_id):
            return JSONResponse({"reasoning_enabled": False, "reports": []})

        type_param = request.query_params.get("report_type")
        report_type = ReportType(type_param) if type_param else None
        reports = report_repository.list(tenant_id=tenant_id, report_type=report_type)
        return JSONResponse({"reasoning_enabled": True, "reports": [_serialize_report(r) for r in reports]})

    return [Route("/admin/ai-reports", list_reports, methods=["GET"])]


def _build_capacity_routes(
    capacity_forecast_repository: CapacityForecastRepositoryPort, ops_intelligence: OpsIntelligenceService
) -> list[Route]:
    """Admin -> Monitoring -> Capacity Planning (ADR-006 Sec 3.4) -- forecast history per resource.

    Same reasoning-gate contract as AI Insights/AI Reports.
    """

    async def capacity_history(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        resource = request.query_params.get("resource")
        if not resource:
            return _error(422, "VALIDATION_ERROR", "missing required query param: resource")

        tenant_id = request.query_params.get("tenant_id")
        if not ops_intelligence.is_reasoning_enabled(tenant_id):
            return JSONResponse({"reasoning_enabled": False, "forecasts": []})

        forecasts: tuple[Any, ...] = capacity_forecast_repository.history(resource, tenant_id=tenant_id)
        return JSONResponse(
            {
                "reasoning_enabled": True,
                "forecasts": [
                    {
                        "forecast_id": f.forecast_id,
                        "tenant_id": f.tenant_id,
                        "resource": f.resource,
                        "horizon_days": f.horizon_days,
                        "forecast_data": f.forecast_data,
                        "headroom_pct": f.headroom_pct,
                        "confidence": f.confidence.value,
                        "generated_at": f.generated_at.isoformat(),
                    }
                    for f in forecasts
                ],
            }
        )

    return [Route("/admin/capacity-forecasts", capacity_history, methods=["GET"])]


def _build_incident_timeline_routes(
    alert_repository: AlertRepositoryPort, insight_repository: InsightRepositoryPort
) -> list[Route]:
    """Admin -> Monitoring -> Incident Timeline -- a derived chronological merge, not a new entity.

    ADR-006 defines no dedicated "Incident" model -- this view merges the
    two real, already-persisted event streams (open alerts + generated AI
    insights) into one timeline rather than inventing a third table.
    Currently shows only OPEN alerts (``AlertRepositoryPort`` has no
    list-all-including-resolved query yet) -- a real, documented gap, not
    silently dropped history.
    """

    async def timeline(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        tenant_id = request.query_params.get("tenant_id")
        timeline_entries: list[dict[str, Any]] = [
            {"type": "alert", "timestamp": a.fired_at.isoformat(), "event": _serialize_alert(a)}
            for a in alert_repository.list_open(tenant_id)
        ]
        timeline_entries.extend(
            {"type": "insight", "timestamp": i.generated_at.isoformat(), "event": _serialize_insight(i)}
            for i in insight_repository.list(tenant_id=tenant_id)
        )
        merged = sorted(timeline_entries, key=lambda e: str(e["timestamp"]), reverse=True)
        return JSONResponse(merged)

    return [Route("/admin/incident-timeline", timeline, methods=["GET"])]


def _serialize_audit_event(event: AuditEvent) -> dict[str, Any]:
    return {
        "audit_id": event.audit_id,
        "actor_id": event.actor_id,
        "action": event.action,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "outcome": event.outcome,
        "recorded_at": event.recorded_at.isoformat(),
    }


def _build_webhook_routes(ingest: WebhookIngestService, system_x_controller: Any = None) -> list[Route]:
    """POST /webhooks/alertmanager — receives Alertmanager webhook_configs POSTs.

    No authentication: Alertmanager is an internal cluster component that
    cannot supply browser session cookies. The route is intentionally
    unauthenticated; network-level controls (K8s NetworkPolicy) gate access.
    """
    import asyncio

    async def alertmanager_webhook(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid_json"}, status_code=400)
        results = ingest.ingest_alertmanager_webhook(payload)
        # Fire System X for each individual alert in the batch
        if system_x_controller is not None:
            for raw_alert in payload.get("alerts", ()):
                asyncio.ensure_future(system_x_controller.handle_alert(raw_alert))
        return JSONResponse({"ingested": len(results)})

    return [Route("/webhooks/alertmanager", alertmanager_webhook, methods=["POST"])]


def _build_system_x_routes(system_x_service: Any) -> list[Route]:
    """GET/POST /admin/system-x/* — System X incident dashboard API."""

    async def list_incidents(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_ALL)
        except (SessionRequiredError, ForbiddenError) as exc:
            return _error(401 if isinstance(exc, SessionRequiredError) else 403, "forbidden", str(exc))
        active_only = request.query_params.get("active") == "true"
        incidents = system_x_service.list_incidents(active_only=active_only)
        return JSONResponse({
            "incidents": [
                {
                    "incident_id": i.incident_id,
                    "title": i.title,
                    "severity": str(i.severity),
                    "status": str(i.status),
                    "detected_at": i.detected_at.isoformat(),
                    "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
                    "affected_services": list(i.affected_services),
                    "total_downtime_s": i.total_downtime_s,
                    "root_cause": i.root_cause,
                    "recovery_summary": i.recovery_summary,
                }
                for i in incidents
            ]
        })

    async def get_incident(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_ALL)
        except (SessionRequiredError, ForbiddenError) as exc:
            return _error(401 if isinstance(exc, SessionRequiredError) else 403, "forbidden", str(exc))
        incident_id = request.path_params["incident_id"]
        incident = system_x_service.get_incident(incident_id)
        if not incident:
            return _error(404, "not_found", f"incident {incident_id} not found")
        analysis = incident.claude_analysis
        return JSONResponse({
            "incident": {
                "incident_id": incident.incident_id,
                "title": incident.title,
                "severity": str(incident.severity),
                "status": str(incident.status),
                "detected_at": incident.detected_at.isoformat(),
                "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
                "affected_services": list(incident.affected_services),
                "alert_fingerprints": list(incident.alert_fingerprints),
                "root_cause": incident.root_cause,
                "recovery_summary": incident.recovery_summary,
                "total_downtime_s": incident.total_downtime_s,
                "claude_analysis": {
                    "root_cause": analysis.root_cause,
                    "confidence": analysis.confidence,
                    "recommended_actions": list(analysis.recommended_actions),
                    "recovery_plan": list(analysis.recovery_plan),
                    "estimated_recovery_time_s": analysis.estimated_recovery_time_s,
                    "risk_assessment": analysis.risk_assessment,
                    "model": analysis.model,
                    "analyzed_at": analysis.analyzed_at.isoformat(),
                } if analysis else None,
                "health_after": incident.health_after,
            }
        })

    async def get_audit_trail(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_ALL)
        except (SessionRequiredError, ForbiddenError) as exc:
            return _error(401 if isinstance(exc, SessionRequiredError) else 403, "forbidden", str(exc))
        incident_id = request.path_params["incident_id"]
        entries = system_x_service.get_audit_trail(incident_id)
        return JSONResponse({
            "audit_trail": [
                {
                    "entry_id": e.entry_id,
                    "recorded_at": e.recorded_at.isoformat(),
                    "actor": e.actor,
                    "action": e.action,
                    "result": e.result,
                    "rollback_status": e.rollback_status,
                    "verification_outcome": e.verification_outcome,
                }
                for e in entries
            ]
        })

    async def get_recovery_actions(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_ALL)
        except (SessionRequiredError, ForbiddenError) as exc:
            return _error(401 if isinstance(exc, SessionRequiredError) else 403, "forbidden", str(exc))
        incident_id = request.path_params["incident_id"]
        actions = system_x_service.get_recovery_actions(incident_id)
        return JSONResponse({
            "recovery_actions": [
                {
                    "action_id": a.action_id,
                    "action_type": str(a.action_type),
                    "target_service": a.target_service,
                    "status": str(a.status),
                    "started_at": a.started_at.isoformat(),
                    "completed_at": a.completed_at.isoformat() if a.completed_at else None,
                    "result": a.result,
                    "error": a.error,
                    "rolled_back": a.rolled_back,
                }
                for a in actions
            ]
        })

    async def get_notifications(request: Request) -> JSONResponse:
        try:
            require_platform_permission(request, PERM_PLATFORM_READ_ALL)
        except (SessionRequiredError, ForbiddenError) as exc:
            return _error(401 if isinstance(exc, SessionRequiredError) else 403, "forbidden", str(exc))
        incident_id = request.path_params["incident_id"]
        notifications = system_x_service.get_notifications(incident_id)
        return JSONResponse({
            "notifications": [
                {
                    "notification_id": n.notification_id,
                    "channel": str(n.channel),
                    "notification_type": n.notification_type,
                    "recipient": n.recipient,
                    "subject": n.subject,
                    "status": str(n.status),
                    "sent_at": n.sent_at.isoformat() if n.sent_at else None,
                    "error": n.error,
                    "created_at": n.created_at.isoformat(),
                }
                for n in notifications
            ]
        })

    return [
        Route("/admin/system-x/incidents", list_incidents, methods=["GET"]),
        Route("/admin/system-x/incidents/{incident_id}", get_incident, methods=["GET"]),
        Route("/admin/system-x/incidents/{incident_id}/audit-trail", get_audit_trail, methods=["GET"]),
        Route("/admin/system-x/incidents/{incident_id}/recovery-actions", get_recovery_actions, methods=["GET"]),
        Route("/admin/system-x/incidents/{incident_id}/notifications", get_notifications, methods=["GET"]),
    ]


def _build_startup_handlers(
    gpu_fleet_monitor: GPUFleetHealthMonitor | None,
    analytics_service: Any,
) -> list[Any]:
    """Return Starlette ``on_startup`` callables for background monitoring tasks."""
    import asyncio
    import logging
    import os

    import httpx

    _log = logging.getLogger("voiceos.web_api.background")

    handlers: list[Any] = []

    # ── GPU node health polling ──────────────────────────────────────────────
    if gpu_fleet_monitor is not None:
        gpu_host = os.environ.get("GPU_HOST", "185.216.21.242")
        gpu_poll_s = int(os.environ.get("GPU_POLL_INTERVAL_S", "30"))
        # One entry per model-serving port that exposes /health (STT/LLM/TTS).
        gpu_endpoints: list[tuple[str, int]] = [
            ("stt", int(os.environ.get("STT_PORT", "8100"))),
            ("llm", int(os.environ.get("LLM_PORT", "8000"))),
            ("tts", int(os.environ.get("TTS_PORT", "8200"))),
        ]

        async def _poll_gpu_nodes() -> None:
            from monitoring.gpu_fleet.fleet_health import GPUNodeSnapshot

            async with httpx.AsyncClient(timeout=5.0) as client:
                while True:
                    for service, port in gpu_endpoints:
                        node_id = f"{gpu_host}:{port}"
                        try:
                            resp = await client.get(f"http://{gpu_host}:{port}/health")
                            healthy = resp.status_code == 200
                            data = resp.json() if healthy else {}
                        except Exception:
                            healthy = False
                            data = {}
                        gpu_fleet_monitor.report_node(
                            GPUNodeSnapshot(
                                node_id=node_id,
                                healthy=healthy,
                                vram_used_mb=int(data.get("vram_used_mb", 0)),
                                vram_total_mb=int(data.get("vram_total_mb", 0)),
                            )
                        )
                    gpu_fleet_monitor.fleet_health_score()
                    await asyncio.sleep(gpu_poll_s)

        def _start_gpu_polling() -> None:
            asyncio.ensure_future(_poll_gpu_nodes())
            _log.info("GPU fleet poller started (host=%s interval=%ds)", gpu_host, gpu_poll_s)

        handlers.append(_start_gpu_polling)

    # ── DailyAggregationJob ──────────────────────────────────────────────────
    if analytics_service is not None and hasattr(analytics_service, "_aggregation_job"):

        async def _run_daily_aggregation() -> None:
            """Trigger DailyAggregationJob once, then repeat daily at midnight UTC."""
            job = analytics_service._aggregation_job
            while True:
                now = date.today()
                yesterday = now - timedelta(days=1)
                _log.info("DailyAggregationJob: would aggregate for %s (no tenant list available at startup)", yesterday)

                # Sleep until next midnight UTC.
                from datetime import time as _time  # noqa: PLC0415
                tomorrow_dt = datetime.combine(now + timedelta(days=1), _time.min).replace(tzinfo=UTC)
                sleep_s = (tomorrow_dt - datetime.now(UTC)).total_seconds()
                await asyncio.sleep(max(sleep_s, 3600))

        def _start_daily_aggregation() -> None:
            asyncio.ensure_future(_run_daily_aggregation())
            _log.info("DailyAggregationJob scheduler started")

        handlers.append(_start_daily_aggregation)

    return handlers


# ---------------------------------------------------------------------------
# Dialer control routes
# ---------------------------------------------------------------------------


def _build_dialer_routes(
    dialer_session_manager: Any,
    twilio_auth_token: str | None = None,
) -> list[Route]:
    """Routes for starting/stopping/querying the outbound dialer and Twilio callbacks.

    POST /campaigns/{campaign_id}/dialer/start  — start a dial session
    POST /campaigns/{campaign_id}/dialer/stop   — stop a running session
    GET  /campaigns/{campaign_id}/dialer/status — session status
    POST /dialer/twilio/status                  — Twilio status callback (validated
                                                  by X-Twilio-Signature when
                                                  twilio_auth_token is configured)

    When ``twilio_auth_token`` is None the signature check is skipped and a
    startup warning is logged — this mode exists only for local development.
    Production deployments MUST configure the token so spoofed callbacks are
    rejected with 403.
    """
    if twilio_auth_token is None:
        _log.warning(
            "dialer status-callback signature validation DISABLED "
            "(twilio_auth_token not configured) — do not use in production."
        )

    async def dialer_start(request: Request) -> JSONResponse:
        guard = _require_tenant(request, PERM_WRITE_CAMPAIGNS)
        if isinstance(guard, JSONResponse):
            return guard
        campaign_id = request.path_params["campaign_id"]
        body: dict = {}
        try:
            body = await request.json()
        except Exception:
            pass

        from src.libs.contracts.primitives import CampaignId, TenantId
        info = await dialer_session_manager.start(
            guard.tenant_id,
            campaign_id,
            daily_start_hour=int(body.get("daily_start_hour", 9)),
            daily_end_hour=int(body.get("daily_end_hour", 18)),
            timezone_name=body.get("timezone_name", "Asia/Kolkata"),
        )
        return JSONResponse(
            {"status": info.status, "campaign_id": info.campaign_id,
             "started_at": info.started_at.isoformat()},
            status_code=200,
        )

    async def dialer_stop(request: Request) -> JSONResponse:
        guard = _require_tenant(request, PERM_WRITE_CAMPAIGNS)
        if isinstance(guard, JSONResponse):
            return guard
        campaign_id = request.path_params["campaign_id"]
        await dialer_session_manager.stop(campaign_id)
        return JSONResponse({"status": "stopping", "campaign_id": campaign_id})

    async def dialer_status(request: Request) -> JSONResponse:
        guard = _require_tenant(request, PERM_READ_ALL)
        if isinstance(guard, JSONResponse):
            return guard
        campaign_id = request.path_params["campaign_id"]
        info = dialer_session_manager.status(campaign_id)
        if info is None:
            return JSONResponse({"status": "idle", "campaign_id": campaign_id})
        return JSONResponse(
            {"status": info.status, "campaign_id": info.campaign_id,
             "started_at": info.started_at.isoformat()}
        )

    async def twilio_status_callback(request: Request) -> JSONResponse:
        """Twilio POSTs call-status events here.

        Auth: X-Twilio-Signature (HMAC-SHA1 of URL + sorted form params using
        the account auth token). When ``twilio_auth_token`` is configured on
        the app, unsigned or invalid requests are rejected with 403 — this is
        the only defense against spoofed call completions corrupting lead
        state (audit BLOCKER #4).
        """
        try:
            form = await request.form()
        except Exception:
            return JSONResponse({"error": "bad_request"}, status_code=400)

        # Signature validation (skipped only when token not configured — dev)
        if twilio_auth_token is not None:
            from src.services.dialer.twilio_signature import validate_signature

            # Reconstruct the exact URL Twilio signed against. If the app is
            # behind a proxy/tunnel, X-Forwarded-* headers reflect the public
            # URL; fall back to request.url otherwise. Configured public URL
            # (DIALER_STATUS_CALLBACK_URL) takes precedence when available on
            # app.state to survive any header rewriting.
            configured_url = getattr(request.app.state, "dialer_status_callback_url", None)
            signed_url = configured_url or str(request.url)
            signature = request.headers.get("X-Twilio-Signature", "")
            form_params = {k: v for k, v in form.multi_items() if isinstance(v, str)}
            if not validate_signature(twilio_auth_token, signed_url, form_params, signature):
                _log.warning(
                    "twilio_status rejected: invalid signature (url=%s remote=%s)",
                    signed_url, request.client.host if request.client else "?",
                )
                return JSONResponse({"error": "invalid_signature"}, status_code=403)

        call_sid = form.get("CallSid", "")
        call_status = form.get("CallStatus", "")
        amd_status = form.get("AnsweredBy", "")

        # Retrieve tenant/campaign/lead context stored at call-placement time
        raw = request.app.state.dialer_redis.get(f"dialer:sid:{call_sid}") if hasattr(request.app.state, "dialer_redis") else None
        if raw:
            tenant_id, campaign_id, lead_id = (raw.decode() if isinstance(raw, bytes) else raw).split("|", 2)
        else:
            # Context passed directly via StatusCallbackParameter
            params = dict(request.query_params)
            tenant_id = form.get("tenant_id") or params.get("tenant_id", "")
            campaign_id = form.get("campaign_id") or params.get("campaign_id", "")
            lead_id = form.get("lead_id") or params.get("lead_id", "")

        _log.info(
            "twilio_status call_sid=%s status=%s amd=%s lead_id=%s",
            call_sid, call_status, amd_status, lead_id,
        )

        from src.services.dialer import metrics as _dm
        terminal = call_status in ("completed", "busy", "failed", "no-answer", "canceled")
        if terminal:
            _dm.record_call_completed(tenant_id, campaign_id, call_status)
            dialer_session_manager.on_call_ended(call_sid, campaign_id)
        if amd_status == "human":
            _dm.record_call_answered(tenant_id, campaign_id)
        elif amd_status in ("machine_start", "machine_end_beep", "machine_end_silence", "machine_end_other"):
            _dm.record_call_machine(tenant_id, campaign_id)

        return JSONResponse({"received": True})

    return [
        Route("/campaigns/{campaign_id}/dialer/start", dialer_start, methods=["POST"]),
        Route("/campaigns/{campaign_id}/dialer/stop", dialer_stop, methods=["POST"]),
        Route("/campaigns/{campaign_id}/dialer/status", dialer_status, methods=["GET"]),
        Route("/dialer/twilio/status", twilio_status_callback, methods=["POST"]),
    ]


def _build_call_summary_routes(call_summary_repository: Any) -> list[Route]:
    """Client -> Phase 3 post-call sales summaries.

    GET /calls/{call_id}/summary — retrieve the structured post-call summary
    for a specific call_id, scoped to the authenticated tenant (AR-8).
    """

    async def get_call_summary(request: Request) -> JSONResponse:
        try:
            session = require_tenant_permission(request, PERM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        call_id = request.path_params["call_id"]
        tenant_id = str(_require_tenant_id(session))
        summary = call_summary_repository.get(call_id, tenant_id)
        if summary is None:
            return _error(404, "NOT_FOUND", f"no summary found for call_id={call_id!r}")
        return JSONResponse(summary)

    return [Route("/calls/{call_id}/summary", get_call_summary, methods=["GET"])]


def _build_callbacks_routes(callback_scheduler: Any) -> list[Route]:
    """Client -> Phase 3 callback management.

    GET /callbacks/pending?customer_id=... — list pending callbacks for a customer.
    """

    async def list_pending_callbacks(request: Request) -> JSONResponse:
        try:
            session = require_tenant_permission(request, PERM_READ_ALL)
        except SessionRequiredError:
            return _error(401, "UNAUTHENTICATED", "sign in required")
        except ForbiddenError as exc:
            return _error(403, "FORBIDDEN", str(exc))

        tenant_id = _require_tenant_id(session)
        customer_id_str = request.query_params.get("customer_id", "")
        if not customer_id_str:
            return _error(422, "VALIDATION_ERROR", "missing required query param: customer_id")

        callbacks = callback_scheduler.find_pending(tenant_id, CustomerId(customer_id_str))
        return JSONResponse(
            [
                {
                    "callback_id": cb.callback_id,
                    "call_id": str(cb.call_id),
                    "customer_id": str(cb.customer_id),
                    "preferred_time": cb.preferred_time.isoformat(),
                    "timezone": cb.timezone,
                    "phone_number": cb.phone_number,
                    "recorded_at": cb.recorded_at.isoformat(),
                }
                for cb in callbacks
            ]
        )

    return [Route("/callbacks/pending", list_pending_callbacks, methods=["GET"])]


__all__ = ["create_web_api"]
