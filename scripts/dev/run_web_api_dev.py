"""Local dev runner for the Web BFF (ADR-005 Sec 4.1) -- real Postgres, stub Google.

Wires the REAL Postgres-backed PlatformUserRepository/UserRepository/
InvitationRepository against POSTGRES_DSN (docker-compose's local Postgres).
Only Google OAuth is stubbed -- that is the one genuinely external,
credential-gated dependency (no real GOOGLE_CLIENT_ID/SECRET available in
this environment); every other component here is production code exercised
against real infrastructure, not a fake.

Usage:
    POSTGRES_DSN=postgresql://voiceos:voiceos_dev_pw@localhost:5432/voiceos_dev \\
    .venv/Scripts/python.exe scripts/dev/run_web_api_dev.py
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg2
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from monitoring.gpu_fleet.fleet_health import GPUFleetHealthMonitor

from src.libs.contracts.models.tenant import Tenant
from src.libs.contracts.models.user import OrgScope
from src.libs.contracts.models.user import Role as DbRole
from src.libs.repositories.admin_audit_view import AdminAuditViewRepository
from src.libs.repositories.analytics import AnalyticsDailyRepository
from src.libs.repositories.audit import AuditRepository
from src.libs.repositories.bi import BIRepository
from src.libs.repositories.billing import BillingRepository, InvoiceRepository, UsageRepository
from src.libs.repositories.call_disposition import CallDispositionRepository
from src.libs.repositories.campaign import CampaignRepository
from src.libs.repositories.campaign_audience import CampaignAudienceRepository
from src.libs.repositories.campaign_result import CampaignResultRepository
from src.libs.repositories.customer import CustomerRepository
from src.libs.repositories.escalation import EscalationRepository
from src.libs.repositories.hitl import HITLDecisionRepository, HITLQueueRepository
from src.libs.repositories.invitation import InvitationRepository
from src.libs.repositories.party import PartyRepository
from src.libs.repositories.platform_user import PlatformUserRepository
from src.libs.repositories.saas_ops import FeatureFlagRepository
from src.libs.repositories.tenant import TenantRepository
from src.libs.repositories.user import UserRepository
from src.services.analytics.aggregation import DailyAggregationJob
from src.services.analytics.call_analytics import CallAnalytics
from src.services.analytics.campaign_analytics import CampaignAnalytics
from src.services.analytics.realtime import RealtimeAnalytics
from src.services.analytics.service import AnalyticsService
from src.services.bi_platform.executive_dashboard import ExecutiveDashboard
from src.services.billing.entitlement import EntitlementEngine
from src.services.billing.invoice import InvoiceGenerator
from src.services.billing.service import BillingService
from src.services.billing.subscription import SubscriptionManager
from src.services.campaign_management.service import CampaignService
from src.services.collections.escalation import EscalationWorkflow
from src.services.compliance_monitoring.service import ComplianceMonitoring
from src.services.crm.repository import CRMRepositories
from src.services.crm.service import CustomerService
from src.services.hitl.override_logger import OverrideLogger
from src.services.hitl.queue import HITLQueue
from src.services.platform_admin.service import PlatformAdminService
from src.services.reporting.exporter import ExportService
from src.services.reporting.scheduler import ReportScheduler
from src.services.reporting.service import ReportingService
from src.services.saas_ops.feature_flags import FeatureFlagService
from src.services.tenant_management.service import TenantService
from src.services.user_management.invitation import InvitationService
from src.services.user_management.service import UserService
from src.services.web_api.api import create_web_api
from src.services.web_api.main import _AlwaysEntitledPolicyPort, _build_ops_intelligence
from src.services.web_api.session import WebSessionCodec

_DEV_EMAIL = "admin@voiceos.ai"
_DEV_TENANT_SLUG = "dev-tenant"
_DEV_TENANT_USER_EMAIL = "manager@dev-tenant.example"
_SYSTEM_ACTOR_ID = "00000000-0000-0000-0000-000000000000"
"""Sentinel UUID for system-initiated writes -- role_assignments.assigned_by is
UUID NOT NULL. Same convention as tenant_management.provisioner.SYSTEM_ACTOR_ID."""


@dataclass(frozen=True)
class _FakeIdentity:
    email: str
    name: str
    email_verified: bool = True


class _StubGoogleOAuth:
    """DEV ONLY -- always resolves code="dev-code" to a fixed identity.

    The only stubbed component in this runner: real Google OAuth requires
    a registered client_id/secret this environment doesn't have. Everything
    downstream of "we have a verified email" (platform_admin lookup, session
    minting, cookie issuance) is real code against the real database.
    """

    def build_authorize_url(self, *, state: str, redirect_uri: str) -> str:
        return f"http://localhost:8100/auth/google/callback?code=dev-code&state={state}"

    async def resolve_verified_email(self, *, code: str, redirect_uri: str) -> _FakeIdentity:
        if code == "dev-code":
            return _FakeIdentity(email=_DEV_EMAIL, name="Dev Admin")
        if code == "dev-tenant-code":
            return _FakeIdentity(email=_DEV_TENANT_USER_EMAIL, name="Dev Tenant Manager")
        raise Exception("dev stub only recognizes code=dev-code or code=dev-tenant-code")


def _seed_dev_platform_user(platform_admin: PlatformAdminService) -> None:
    """Idempotently ensure the dev platform admin exists in the real database."""
    if platform_admin.find_by_email(_DEV_EMAIL) is not None:
        print(f"Dev platform user already exists: {_DEV_EMAIL}")
        return
    from src.services.platform_admin.roles import PlatformRole

    platform_admin.create(email=_DEV_EMAIL, name="Dev Admin", platform_role=PlatformRole.PLATFORM_ADMIN)
    print(f"Seeded platform user: {_DEV_EMAIL} (PLATFORM_ADMIN)")


def _seed_dev_tenant_and_role(tenant_service: TenantService, user_repo: UserRepository) -> tuple[Tenant, DbRole]:
    """Idempotently ensure a dev tenant + a real MANAGER role exist in the real database.

    Creates a genuine ``roles`` table row (role_assignments.role_id is a real
    UUID FK -- discovered via live testing, see ADR-005 implementation notes)
    with the same permission set ``authz.roles.ROLE_PERMISSIONS[Role.MANAGER]``
    defines, mirroring how ``TenantProvisioner._create_default_admin()`` seeds
    the ADMIN system role at real tenant provisioning.
    """
    from src.services.authz.roles import ROLE_PERMISSIONS
    from src.services.authz.roles import Role as AuthzRole

    tenant = tenant_service.get_by_slug(_DEV_TENANT_SLUG)
    if tenant is None:
        tenant = tenant_service.create_tenant(_DEV_TENANT_SLUG, "Dev Tenant", "GROWTH")
        print(f"Seeded tenant: {_DEV_TENANT_SLUG} ({tenant.tenant_id})")
    else:
        print(f"Dev tenant already exists: {_DEV_TENANT_SLUG} ({tenant.tenant_id})")

    now = datetime.now(UTC)
    role = user_repo.get_role_by_name(tenant.tenant_id, "MANAGER")
    if role is None:
        role = DbRole(
            role_id=str(uuid.uuid4()),
            tenant_id=tenant.tenant_id,
            name="MANAGER",
            description="Dev-seeded system role",
            permissions=tuple(ROLE_PERMISSIONS[AuthzRole.MANAGER]),
            is_system_role=True,
            created_at=now,
            updated_at=now,
        )
        user_repo.create_role(role)
        print(f"Seeded role: MANAGER ({role.role_id}) permissions={role.permissions}")
    else:
        print(f"Dev MANAGER role already exists: {role.role_id}")

    return tenant, role


def _seed_dev_hitl_item(hitl_queue: HITLQueue, tenant: Tenant) -> None:
    """Idempotently ensure at least one PENDING HITL item exists for the dev tenant."""
    from src.libs.contracts.models.hitl import HITLPriority

    if hitl_queue.list_pending(tenant.tenant_id):
        print("Dev HITL queue already has pending items")
        return
    item = hitl_queue.enqueue(
        tenant.tenant_id,
        call_id="dev-call-0001",
        reason="Customer disputed debt validity -- REQUIRE_HUMAN per Risk Engine",
        priority=HITLPriority.HIGH,
        context={"risk_score": 0.82, "violations": []},
    )
    print(f"Seeded HITL item: {item.hitl_item_id} (HIGH priority)")


def _issue_dev_tenant_invitation(
    invitation_service: InvitationService, tenant: Tenant, role: DbRole
) -> str | None:
    """Issue a fresh invitation for the dev tenant user via the REAL invitation
    flow (not direct user creation) -- a returning tenant user with no
    invitation token is an intentionally unresolved login path (see api.py's
    module docstring), so testing through invitation-acceptance is both the
    correct and the only currently-supported way to get a real tenant session.

    Returns the raw token to use as ?invitation_token=... against
    /auth/google/start, or None if a user with this email already exists
    (idempotent -- an already-activated invitation cannot be reissued).
    """
    issued = invitation_service.invite(
        tenant.tenant_id,
        _DEV_TENANT_USER_EMAIL,
        role.role_id,
        OrgScope(scope_type="TENANT", scope_id=str(tenant.tenant_id)),
        invited_by=_SYSTEM_ACTOR_ID,
    )
    return issued.raw_token


def main() -> None:
    dsn = os.environ.get("POSTGRES_DSN", "postgresql://voiceos:voiceos_dev_pw@localhost:5432/voiceos_dev")
    conn = psycopg2.connect(dsn)

    platform_admin = PlatformAdminService(PlatformUserRepository(conn))
    _seed_dev_platform_user(platform_admin)

    user_repo = UserRepository(conn)
    invitation_service = InvitationService(InvitationRepository(conn), user_repo)
    user_service = UserService(user_repo, invitation_service)
    tenant_service = TenantService(TenantRepository(conn))
    campaign_service = CampaignService(CampaignRepository(conn))

    tenant, role = _seed_dev_tenant_and_role(tenant_service, user_repo)
    raw_invitation_token = _issue_dev_tenant_invitation(invitation_service, tenant, role)

    hitl_queue = HITLQueue(HITLQueueRepository(conn))
    override_logger = OverrideLogger(HITLDecisionRepository(conn), HITLQueueRepository(conn))
    _seed_dev_hitl_item(hitl_queue, tenant)

    admin_audit_repository = AdminAuditViewRepository(conn)
    audit_repository = AuditRepository(conn)
    crm_service = CustomerService(CRMRepositories(CustomerRepository(conn), PartyRepository(conn)))
    collections_workflow = EscalationWorkflow(EscalationRepository(conn))
    campaign_audience_repository = CampaignAudienceRepository(conn)

    billing_service = BillingService(
        SubscriptionManager(BillingRepository(conn)),
        EntitlementEngine(_AlwaysEntitledPolicyPort()),
        InvoiceGenerator(UsageRepository(conn), InvoiceRepository(conn)),
    )
    feature_flag_service = FeatureFlagService(FeatureFlagRepository(conn))
    compliance_monitoring = ComplianceMonitoring.create()
    gpu_fleet_monitor = GPUFleetHealthMonitor()
    executive_dashboard = ExecutiveDashboard(BIRepository(conn))

    call_analytics = CallAnalytics(CallDispositionRepository(conn))
    campaign_analytics = CampaignAnalytics(CampaignResultRepository(conn))
    aggregation_job = DailyAggregationJob(
        CallDispositionRepository(conn), CampaignResultRepository(conn), AnalyticsDailyRepository(conn)
    )
    analytics_service = AnalyticsService(
        call_analytics, campaign_analytics, RealtimeAnalytics(call_analytics, campaign_analytics), aggregation_job
    )
    reporting_service = ReportingService(ReportScheduler(aggregation_job), ExportService())

    (
        ops_intelligence,
        alert_repository,
        alert_lifecycle,
        insight_repository,
        report_repository,
        capacity_forecast_repository,
    ) = _build_ops_intelligence(conn, feature_flag_service)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    session_codec = WebSessionCodec(private_key, private_key.public_key())

    from src.libs.health.aggregator import HealthAggregator
    from src.services.web_api.health_checks import PostgresHealthCheck

    health_aggregator = HealthAggregator([PostgresHealthCheck(conn)])

    app = create_web_api(
        session_codec=session_codec,
        google_oauth=_StubGoogleOAuth(),
        platform_admin=platform_admin,
        user_service=user_service,
        tenant_service=tenant_service,
        campaign_service=campaign_service,
        hitl_queue=hitl_queue,
        override_logger=override_logger,
        admin_audit_repository=admin_audit_repository,
        audit_repository=audit_repository,
        crm_service=crm_service,
        collections_workflow=collections_workflow,
        campaign_audience_repository=campaign_audience_repository,
        billing_service=billing_service,
        feature_flag_service=feature_flag_service,
        compliance_monitoring=compliance_monitoring,
        gpu_fleet_monitor=gpu_fleet_monitor,
        executive_dashboard=executive_dashboard,
        analytics_service=analytics_service,
        reporting_service=reporting_service,
        ops_intelligence=ops_intelligence,
        alert_repository=alert_repository,
        alert_lifecycle=alert_lifecycle,
        insight_repository=insight_repository,
        report_repository=report_repository,
        capacity_forecast_repository=capacity_forecast_repository,
        frontend_base_url="http://localhost:3000",
        bff_public_url="http://localhost:8100",
        health_aggregator=health_aggregator,
        cookie_secure=False,
    )
    print("Real Postgres-backed services. Only Google OAuth is stubbed.")
    print("Dev Google stub: code=dev-code -> " + _DEV_EMAIL + " (platform admin)")
    print(
        "Dev Google stub: code=dev-tenant-code -> "
        + _DEV_TENANT_USER_EMAIL
        + " (tenant MANAGER, via invitation acceptance)"
    )
    print(f"Fresh invitation token (single use): {raw_invitation_token}")
    print(
        "To get a real tenant session: GET /auth/google/start?invitation_token="
        f"{raw_invitation_token} , follow the redirect chain to /auth/google/callback?code=dev-tenant-code"
    )
    uvicorn.run(app, host="127.0.0.1", port=8100, log_level="info")


if __name__ == "__main__":
    main()
