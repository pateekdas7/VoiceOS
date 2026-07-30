"""Production entrypoint for the Web BFF (ADR-005 Sec 4.1).

Wires real Postgres-backed repositories and a real Google OAuth client from
environment configuration. No standalone HTTP listener is started at import
time -- ``uvicorn.run(create_app())`` is the caller's responsibility, same
"library-class service" precedent as every other service in this codebase
(``api_platform``, ``admin_portal``).

Required environment variables:
    POSTGRES_DSN          -- e.g. postgresql://user:pass@host:5432/db
    GOOGLE_CLIENT_ID
    GOOGLE_CLIENT_SECRET
    FRONTEND_BASE_URL     -- e.g. https://app.voiceos.ai
    BFF_PUBLIC_URL        -- e.g. https://api.voiceos.ai/web

Optional:
    WEB_API_PRIVATE_KEY_PATH / WEB_API_PUBLIC_KEY_PATH -- PEM paths for the
        session-signing RSA keypair. If unset, a fresh keypair is generated
        at process start -- fine for local dev (single process, restart
        invalidates all sessions) but NOT for a real multi-instance
        production deployment, which must provision a persistent keypair
        via the existing secrets infrastructure (src.libs.secrets) rather
        than each instance minting its own.
    PROMETHEUS_URL / LOKI_URL / JAEGER_URL -- override the in-cluster
        default DNS names ``observability_clients.py`` points at, for
        environments where those hostnames don't resolve (e.g. local dev
        against docker-compose's Prometheus on localhost).
"""

from __future__ import annotations

import os

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from monitoring.gpu_fleet.fleet_health import GPUFleetHealthMonitor
from starlette.applications import Starlette

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
from src.services.ops_intelligence.plumbing.alert_lifecycle import AlertLifecycleService
from src.services.ops_intelligence.reasoning.adapters.protocol import NarrationRequest, NarrationResult
from src.services.ops_intelligence.reasoning.capacity_planner import CapacityPlanner
from src.services.ops_intelligence.reasoning.evidence_bundler import EvidenceBundler
from src.services.ops_intelligence.reasoning.insight_service import InsightService
from src.services.ops_intelligence.reasoning.observability_clients import (
    DEFAULT_JAEGER_BASE_URL,
    DEFAULT_LOKI_BASE_URL,
    DEFAULT_PROMETHEUS_BASE_URL,
    JaegerQueryAdapter,
    LokiQueryAdapter,
    PrometheusQueryAdapter,
)
from src.services.ops_intelligence.reasoning.report_generator import ReportGenerator
from src.services.ops_intelligence.repositories import (
    PostgresAlertRepository,
    PostgresCapacityForecastRepository,
    PostgresInsightRepository,
    PostgresPatternSignatureRepository,
    PostgresReportRepository,
)
from src.services.ops_intelligence.service import OpsIntelligenceService
from src.services.platform_admin.service import PlatformAdminService
from src.services.policy_engine.decision import PolicyDecision, PolicyOutcome
from src.services.reporting.exporter import ExportService
from src.services.reporting.scheduler import ReportScheduler
from src.services.reporting.service import ReportingService
from src.services.saas_ops.feature_flags import FeatureFlagService
from src.services.tenant_management.service import TenantService
from src.services.user_management.invitation import InvitationService
from src.services.user_management.service import UserService

from .api import create_web_api
from .google_oauth import GoogleOAuthClient
from .health_checks import PostgresHealthCheck, RedisHealthCheck
from .session import WebSessionCodec


class MissingConfigError(RuntimeError):
    """Raised when a required environment variable is unset."""


class _AlwaysEntitledPolicyPort:
    """Placeholder ``PolicyEntitlementPort`` -- BillingService requires an
    EntitlementEngine at construction, but none of this pass's BFF billing
    routes (subscription read/create, invoice generation) call
    ``check_entitlement()``. Wiring the real PDP (``policy_engine``) here is
    a separate composition-root concern belonging to whichever sprint
    exposes usage-limit enforcement through the BFF -- this stub keeps that
    decision open rather than silently hardcoding an answer for it.
    """

    def check_entitlement(
        self,
        tenant_id: str,
        feature: str,
        tier: str,
        usage_quantity: int | None = None,
        usage_limit: int | None = None,
        trial_expired: bool = False,
        subject: str = "billing_service",
    ) -> PolicyDecision:
        return PolicyDecision(outcome=PolicyOutcome.PERMIT, reason="entitlement PDP not wired in this environment")


class _UnreachableReasoningAdapter:
    """Placeholder ``ReasoningAdapter`` -- required by ``InsightService``/
    ``ReportGenerator`` at construction, but never actually invoked: both are
    only reachable through ``OpsIntelligenceService``, which checks the
    ``ops_intelligence_reasoning`` feature flag (default state: no row =
    disabled) before touching either. Wiring a real Anthropic-backed
    ``ClaudeAdapter`` requires live credentials (``src.libs.secrets``) this
    environment does not have -- turning the flag on in a real deployment
    is exactly the point at which this placeholder must be replaced.
    """

    async def narrate(self, request: NarrationRequest) -> NarrationResult:
        raise NotImplementedError(
            "reasoning adapter not configured -- this must never be reached while "
            "the ops_intelligence_reasoning flag is disabled"
        )


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise MissingConfigError(f"{name} is required to start the Web BFF")
    return value


def _load_or_generate_keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    private_path = os.environ.get("WEB_API_PRIVATE_KEY_PATH")
    public_path = os.environ.get("WEB_API_PUBLIC_KEY_PATH")
    if private_path and public_path and os.path.exists(private_path) and os.path.exists(public_path):
        with open(private_path, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
        with open(public_path, "rb") as f:
            public_key = serialization.load_pem_public_key(f.read())
        assert isinstance(private_key, rsa.RSAPrivateKey)
        assert isinstance(public_key, rsa.RSAPublicKey)
        return private_key, public_key

    import logging

    logging.getLogger(__name__).warning(
        "WEB_API_PRIVATE_KEY_PATH/WEB_API_PUBLIC_KEY_PATH not configured -- generating an ephemeral "
        "session-signing keypair for this process. Every existing session is invalidated on restart, "
        "and this is unsafe for a multi-instance deployment. Provision a persistent keypair via "
        "src.libs.secrets before running more than one instance."
    )
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _build_ops_intelligence(conn: object, feature_flag_service: FeatureFlagService) -> tuple[
    OpsIntelligenceService,
    PostgresAlertRepository,
    AlertLifecycleService,
    PostgresInsightRepository,
    PostgresReportRepository,
    PostgresCapacityForecastRepository,
]:
    """Compose the ops_intelligence facade + its repositories (ADR-006)."""
    prometheus = PrometheusQueryAdapter(os.environ.get("PROMETHEUS_URL", DEFAULT_PROMETHEUS_BASE_URL))
    loki = LokiQueryAdapter(os.environ.get("LOKI_URL", DEFAULT_LOKI_BASE_URL))
    jaeger = JaegerQueryAdapter(os.environ.get("JAEGER_URL", DEFAULT_JAEGER_BASE_URL))

    insight_repository = PostgresInsightRepository(conn)
    report_repository = PostgresReportRepository(conn)
    capacity_repository = PostgresCapacityForecastRepository(conn)
    pattern_repository = PostgresPatternSignatureRepository(conn)
    alert_repository = PostgresAlertRepository(conn)
    alert_lifecycle = AlertLifecycleService(alert_repository)

    reasoning_adapter = _UnreachableReasoningAdapter()
    evidence_bundler = EvidenceBundler(prometheus, loki, jaeger)
    insight_service = InsightService(insight_repository, reasoning_adapter, pattern_repository)
    report_generator = ReportGenerator(report_repository, reasoning_adapter)
    capacity_planner = CapacityPlanner(prometheus, capacity_repository)

    ops_intelligence = OpsIntelligenceService(
        feature_flag_service, evidence_bundler, insight_service, report_generator, capacity_planner
    )
    return ops_intelligence, alert_repository, alert_lifecycle, insight_repository, report_repository, capacity_repository


def create_app() -> Starlette:
    """Assemble the production Web BFF app from environment configuration."""
    import psycopg2

    dsn = _require_env("POSTGRES_DSN")
    google_client_id = _require_env("GOOGLE_CLIENT_ID")
    google_client_secret = _require_env("GOOGLE_CLIENT_SECRET")
    frontend_base_url = _require_env("FRONTEND_BASE_URL")
    bff_public_url = _require_env("BFF_PUBLIC_URL")

    conn = psycopg2.connect(dsn)
    platform_admin = PlatformAdminService(PlatformUserRepository(conn))
    user_repo = UserRepository(conn)
    invitation_service = InvitationService(InvitationRepository(conn), user_repo)
    user_service = UserService(user_repo, invitation_service)
    tenant_service = TenantService(TenantRepository(conn))
    campaign_service = CampaignService(CampaignRepository(conn))
    hitl_queue = HITLQueue(HITLQueueRepository(conn))
    override_logger = OverrideLogger(HITLDecisionRepository(conn), HITLQueueRepository(conn))

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

    private_key, public_key = _load_or_generate_keypair()
    session_codec = WebSessionCodec(private_key, public_key)

    google_oauth = GoogleOAuthClient(google_client_id, google_client_secret, httpx.AsyncClient())

    from src.libs.health.aggregator import HealthAggregator
    from src.libs.health.protocol import HealthCheck

    health_checks: list[HealthCheck] = [PostgresHealthCheck(conn)]
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        import redis

        health_checks.append(RedisHealthCheck(redis.Redis.from_url(redis_url)))
    health_aggregator = HealthAggregator(health_checks)

    from src.services.system_x import SystemXService

    system_x_service = SystemXService(conn)

    return create_web_api(
        session_codec=session_codec,
        google_oauth=google_oauth,
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
        system_x_service=system_x_service,
        frontend_base_url=frontend_base_url,
        bff_public_url=bff_public_url,
        health_aggregator=health_aggregator,
        cookie_secure=os.environ.get("WEB_API_COOKIE_SECURE", "true").lower() != "false",
    )


__all__ = ["MissingConfigError", "create_app"]
