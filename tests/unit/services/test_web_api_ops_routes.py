"""Unit tests for the audit-driven "complete the frontend" pass: Admin Users &
Roles / Audit Logs / Security / Compliance / Infrastructure / Analytics /
Billing / Platform Settings, and Client CRM / Collections / Leads / Reports /
Analytics, and Admin Monitoring (Alerts / AI Insights / AI Reports / Capacity
Planning / Incident Timeline).

All tests run fully in-process against a real Starlette TestClient with fake
repository ports -- no live Postgres/network required (Phase 1), same
convention as test_web_api_campaigns.py / test_web_api_hitl.py. Every route
here was additionally live-verified against real Postgres via curl during
implementation (see PROJECT_STATUS.md) -- these tests are the fast,
DB-independent regression layer on top of that.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from monitoring.gpu_fleet.fleet_health import GPUFleetHealthMonitor
from starlette.testclient import TestClient

from src.libs.contracts.models.bi import BIFactDaily
from src.libs.contracts.models.billing import BillingSubscription, Invoice
from src.libs.contracts.models.collections import EscalationRecord
from src.libs.contracts.models.customer import Customer
from src.libs.contracts.models.platform_user import PlatformUser
from src.libs.contracts.models.saas_ops import FeatureFlag, FeatureFlagScope
from src.libs.contracts.primitives import CallId, CustomerId, TenantId
from src.services.analytics.aggregation import DailyAggregationJob
from src.services.analytics.call_analytics import CallAnalytics
from src.services.analytics.campaign_analytics import CampaignAnalytics
from src.services.analytics.realtime import RealtimeAnalytics
from src.services.analytics.service import AnalyticsService
from src.services.authz.roles import ROLE_PERMISSIONS
from src.services.authz.roles import Role as AuthzRole
from src.services.bi_platform.executive_dashboard import ExecutiveDashboard
from src.services.billing.entitlement import EntitlementEngine
from src.services.billing.invoice import InvoiceGenerator
from src.services.billing.service import BillingService
from src.services.billing.subscription import SubscriptionManager
from src.services.collections.escalation import EscalationWorkflow
from src.services.compliance_monitoring.service import ComplianceMonitoring
from src.services.crm.repository import CRMRepositories
from src.services.crm.service import CustomerService
from src.services.ops_intelligence.models import (
    AlertRecord,
    AlertSource,
    AlertStatus,
    Insight,
    Severity,
)
from src.services.ops_intelligence.plumbing.alert_lifecycle import AlertLifecycleService
from src.services.ops_intelligence.reasoning.capacity_planner import CapacityForecast
from src.services.ops_intelligence.service import OpsIntelligenceService
from src.services.platform_admin.service import PlatformAdminService
from src.services.reporting.exporter import ExportService
from src.services.reporting.scheduler import ReportScheduler
from src.services.reporting.service import ReportingService
from src.services.saas_ops.feature_flags import FeatureFlagService
from src.services.user_management.invitation import InvitationService
from src.services.user_management.service import UserService
from src.services.web_api.api import create_web_api
from src.services.web_api.session import WebSessionCodec

FRONTEND_URL = "https://app.voiceos.test"
BFF_URL = "https://bff.voiceos.test"
TENANT_A = "tenant-a"


# ---------------------------------------------------------------------------
# Minimal no-op fakes shared by every test client (auth plumbing only)
# ---------------------------------------------------------------------------


class _NoopPlatformUserRepository:
    def __init__(self) -> None:
        self.users: list[PlatformUser] = []

    def create(self, user: PlatformUser) -> PlatformUser:
        self.users.append(user)
        return user

    def get(self, platform_user_id: str) -> PlatformUser | None:
        return next((u for u in self.users if u.platform_user_id == platform_user_id), None)

    def find_by_email(self, email: str) -> PlatformUser | None:
        return next((u for u in self.users if u.email == email), None)

    def list_all(self) -> tuple[PlatformUser, ...]:
        return tuple(self.users)

    def set_active_status(self, platform_user_id: str, *, is_active: bool) -> int:
        return 0


class _NoopUserRepository:
    def create_user(self, user: Any) -> Any:
        return user

    def assign_role(self, assignment: Any) -> Any:
        return assignment

    def get_user(self, tenant_id: Any, user_id: str) -> None:
        return None

    def find_user_by_email(self, tenant_id: Any, email: str) -> None:
        return None

    def list_users(self, tenant_id: Any) -> tuple[Any, ...]:
        return ()

    def set_active_status(self, tenant_id: Any, user_id: str, *, is_active: bool) -> int:
        return 0

    def get_role(self, tenant_id: Any, role_id: str) -> Any:
        return None

    def get_role_by_name(self, tenant_id: Any, name: str) -> Any:
        return None

    def list_roles(self, tenant_id: Any) -> tuple[Any, ...]:
        return ()

    def create_role(self, role: Any) -> Any:
        return role


class _NoopInvitationRepository:
    def create(self, invitation: Any) -> Any:
        return invitation

    def get_by_token_hash(self, token_hash: str) -> None:
        return None

    def mark_status(self, invitation_id: str, status: str, *, accepted_at: Any = None) -> int:
        return 0


class _NoopGoogleOAuth:
    def build_authorize_url(self, *, state: str, redirect_uri: str) -> str:
        return "https://accounts.google.com/fake"

    async def resolve_verified_email(self, *, code: str, redirect_uri: str) -> Any:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Domain fakes
# ---------------------------------------------------------------------------


class _FakeAdminAuditViewRepository:
    def __init__(self) -> None:
        self.events: dict[str, list[Any]] = {}

    def list_for_tenant(self, tenant_id: str) -> tuple[Any, ...]:
        return tuple(self.events.get(tenant_id, []))


class _FakeAuditRepository:
    def __init__(self) -> None:
        self.rows: dict[str, list[tuple[Any, ...]]] = {}

    def iter_chain(self, tenant_id: str, start: Any = None, end: Any = None) -> tuple[Any, ...]:
        return tuple(self.rows.get(tenant_id, []))


class _FakeCustomerRepository:
    def __init__(self) -> None:
        self.store: dict[str, Customer] = {}

    def create(self, customer: Customer) -> Customer:
        self.store[customer.customer_id] = customer
        return customer

    def get(self, tenant_id: str, customer_id: str) -> Customer | None:
        c = self.store.get(customer_id)
        return c if c is not None and c.tenant_id == tenant_id else None

    def find_by_external_id(self, tenant_id: str, crm_id: str) -> Customer | None:
        return next((c for c in self.store.values() if c.tenant_id == tenant_id and c.crm_id == crm_id), None)

    def find_by_phone(self, tenant_id: str, phone: str) -> Customer | None:
        return None

    def list_for_tenant(self, tenant_id: str, limit: int = 200) -> tuple[Customer, ...]:
        return tuple(c for c in self.store.values() if c.tenant_id == tenant_id)


class _FakePartyRepository:
    pass


class _FakeEscalationRepository:
    def __init__(self) -> None:
        self.store: dict[str, EscalationRecord] = {}

    def create(self, escalation: EscalationRecord) -> EscalationRecord:
        self.store[escalation.escalation_id] = escalation
        return escalation

    def find_by_call(self, tenant_id: str, call_id: str) -> tuple[EscalationRecord, ...]:
        return tuple(e for e in self.store.values() if e.tenant_id == tenant_id and e.call_id == call_id)

    def list_for_tenant(self, tenant_id: str, limit: int = 200) -> tuple[EscalationRecord, ...]:
        return tuple(e for e in self.store.values() if e.tenant_id == tenant_id)

    def resolve(self, tenant_id: str, escalation_id: str, resolved_at: Any, resolution_notes: str) -> None:
        e = self.store[escalation_id]
        self.store[escalation_id] = e.model_copy(update={"resolved_at": resolved_at, "resolution_notes": resolution_notes})


class _FakeCampaignAudienceRepository:
    def find_by_campaign(self, tenant_id: str, campaign_id: str) -> tuple[Any, ...]:
        return ()


class _FakeBillingRepository:
    def __init__(self) -> None:
        self.store: dict[str, BillingSubscription] = {}

    def create_subscription(self, subscription: BillingSubscription) -> BillingSubscription:
        self.store[subscription.tenant_id] = subscription
        return subscription

    def get_subscription(self, tenant_id: str) -> BillingSubscription | None:
        return self.store.get(tenant_id)


class _FakeUsageRepository:
    def find_between(self, *args: Any, **kwargs: Any) -> tuple[Any, ...]:
        return ()


class _FakeInvoiceRepository:
    def create(self, invoice: Invoice) -> Invoice:
        return invoice


class _FakePolicyEntitlementPort:
    def check_entitlement(self, *args: Any, **kwargs: Any) -> Any:
        from src.services.policy_engine.decision import PolicyDecision, PolicyOutcome

        return PolicyDecision(outcome=PolicyOutcome.PERMIT)


class _FakeFeatureFlagRepository:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str | None], FeatureFlag] = {}

    def upsert(self, flag: FeatureFlag) -> FeatureFlag:
        self.rows[(flag.flag_name, flag.scope.value, flag.scope_value)] = flag
        return flag

    def get(self, flag_name: str, scope: FeatureFlagScope, scope_value: str | None) -> FeatureFlag | None:
        return self.rows.get((flag_name, scope.value, scope_value))

    def list_for_flag(self, flag_name: str) -> tuple[FeatureFlag, ...]:
        return tuple(f for (name, _, _), f in self.rows.items() if name == flag_name)


class _FakeBIRepository:
    def find_fact_for_tenant(self, tenant_id: str, day: Any) -> BIFactDaily | None:
        return None


class _FakeCallDispositionRepository:
    def find_between(self, *args: Any, **kwargs: Any) -> tuple[Any, ...]:
        return ()


class _FakeCampaignResultRepository:
    def find_by_campaign(self, *args: Any, **kwargs: Any) -> tuple[Any, ...]:
        return ()

    def find_between(self, *args: Any, **kwargs: Any) -> tuple[Any, ...]:
        return ()


class _FakeAnalyticsDailyRepository:
    def upsert(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class _FakeAlertRepository:
    def __init__(self) -> None:
        self.store: dict[str, AlertRecord] = {}

    def create(self, alert: AlertRecord) -> AlertRecord:
        self.store[alert.alert_id] = alert
        return alert

    def get(self, alert_id: str) -> AlertRecord | None:
        return self.store.get(alert_id)

    def find_by_fingerprint_open(self, fingerprint: str) -> AlertRecord | None:
        return next((a for a in self.store.values() if a.fingerprint == fingerprint and a.status != AlertStatus.RESOLVED), None)

    def update_status(self, alert_id: str, alert: AlertRecord) -> AlertRecord:
        self.store[alert_id] = alert
        return alert

    def list_open(self, tenant_id: str | None = None) -> tuple[AlertRecord, ...]:
        return tuple(a for a in self.store.values() if a.status != AlertStatus.RESOLVED)

    def count_by_status(self) -> dict[tuple[AlertStatus, str], int]:
        return {}


class _FakeInsightRepository:
    def __init__(self, insights: tuple[Insight, ...] = ()) -> None:
        self._insights = insights

    def create(self, insight: Insight) -> Insight:
        return insight

    def get(self, insight_id: str) -> Insight | None:
        return None

    def list(self, *, tenant_id: str | None = None, category: Any = None, limit: int = 50) -> tuple[Insight, ...]:
        return self._insights


class _FakeReportRepository:
    def create(self, report: Any) -> Any:
        return report

    def get(self, report_id: str) -> Any:
        return None

    def list(self, *, tenant_id: str | None = None, report_type: Any = None, limit: int = 50) -> tuple[Any, ...]:
        return ()


class _FakeCapacityForecastRepository:
    def __init__(self, forecasts: tuple[CapacityForecast, ...] = ()) -> None:
        self._forecasts = forecasts

    def create(self, forecast: CapacityForecast) -> CapacityForecast:
        return forecast

    def latest(self, resource: str, *, tenant_id: str | None = None) -> CapacityForecast | None:
        return self._forecasts[0] if self._forecasts else None

    def history(self, resource: str, *, tenant_id: str | None = None, limit: int = 12) -> tuple[CapacityForecast, ...]:
        return self._forecasts


@pytest.fixture
def session_codec() -> WebSessionCodec:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return WebSessionCodec(private_key, private_key.public_key())


def _platform_token(codec: WebSessionCodec, role: str = "PLATFORM_ADMIN") -> str:
    from src.services.platform_admin.roles import PLATFORM_ROLE_PERMISSIONS, PlatformRole

    permissions = tuple(PLATFORM_ROLE_PERMISSIONS[PlatformRole(role)])
    return codec.encode(actor_kind="platform", subject="pu-1", role=role, permissions=permissions, email="a@voiceos.ai", tenant_id=None)


def _tenant_token(codec: WebSessionCodec, role: str = "ADMIN") -> str:
    permissions = tuple(ROLE_PERMISSIONS.get(AuthzRole(role), frozenset()))
    return codec.encode(actor_kind="tenant", subject="u-1", role=role, permissions=permissions, email="u@tenant.com", tenant_id=TENANT_A)


@pytest.fixture
def deps() -> dict[str, Any]:
    customer_repo = _FakeCustomerRepository()
    escalation_repo = _FakeEscalationRepository()
    audience_repo = _FakeCampaignAudienceRepository()
    admin_audit_repo = _FakeAdminAuditViewRepository()
    audit_repo = _FakeAuditRepository()
    billing_repo = _FakeBillingRepository()
    flag_repo = _FakeFeatureFlagRepository()
    alert_repo = _FakeAlertRepository()

    call_analytics = CallAnalytics(_FakeCallDispositionRepository())
    campaign_analytics = CampaignAnalytics(_FakeCampaignResultRepository())
    aggregation_job = DailyAggregationJob(
        _FakeCallDispositionRepository(), _FakeCampaignResultRepository(), _FakeAnalyticsDailyRepository()
    )

    feature_flag_service = FeatureFlagService(flag_repo)

    return {
        "customer_repo": customer_repo,
        "escalation_repo": escalation_repo,
        "admin_audit_repo": admin_audit_repo,
        "audit_repo": audit_repo,
        "billing_repo": billing_repo,
        "flag_repo": flag_repo,
        "alert_repo": alert_repo,
        "platform_admin": PlatformAdminService(_NoopPlatformUserRepository()),
        "crm_service": CustomerService(CRMRepositories(customer_repo, _FakePartyRepository())),  # type: ignore[arg-type]
        "collections_workflow": EscalationWorkflow(escalation_repo),  # type: ignore[arg-type]
        "campaign_audience_repository": audience_repo,
        "billing_service": BillingService(
            SubscriptionManager(billing_repo),
            EntitlementEngine(_FakePolicyEntitlementPort()),
            InvoiceGenerator(_FakeUsageRepository(), _FakeInvoiceRepository()),  # type: ignore[arg-type]
        ),
        "feature_flag_service": feature_flag_service,
        "compliance_monitoring": ComplianceMonitoring.create(),
        "gpu_fleet_monitor": GPUFleetHealthMonitor(),
        "executive_dashboard": ExecutiveDashboard(_FakeBIRepository()),
        "analytics_service": AnalyticsService(
            call_analytics, campaign_analytics, RealtimeAnalytics(call_analytics, campaign_analytics), aggregation_job
        ),
        "reporting_service": ReportingService(ReportScheduler(aggregation_job), ExportService()),
        "alert_lifecycle": AlertLifecycleService(alert_repo),
    }


@pytest.fixture
def app_client(session_codec: WebSessionCodec, deps: dict[str, Any]) -> TestClient:
    platform_admin = deps["platform_admin"]
    invitation_service = InvitationService(_NoopInvitationRepository(), _NoopUserRepository())
    user_service = UserService(_NoopUserRepository(), invitation_service)

    ops_intelligence = OpsIntelligenceService(
        deps["feature_flag_service"],
        evidence_bundler=object(),  # type: ignore[arg-type]
        insight_service=object(),  # type: ignore[arg-type]
    )

    app = create_web_api(
        session_codec=session_codec,
        google_oauth=_NoopGoogleOAuth(),
        platform_admin=platform_admin,
        user_service=user_service,
        admin_audit_repository=deps["admin_audit_repo"],
        audit_repository=deps["audit_repo"],
        crm_service=deps["crm_service"],
        collections_workflow=deps["collections_workflow"],
        billing_service=deps["billing_service"],
        feature_flag_service=deps["feature_flag_service"],
        compliance_monitoring=deps["compliance_monitoring"],
        gpu_fleet_monitor=deps["gpu_fleet_monitor"],
        executive_dashboard=deps["executive_dashboard"],
        analytics_service=deps["analytics_service"],
        reporting_service=deps["reporting_service"],
        ops_intelligence=ops_intelligence,
        alert_repository=deps["alert_repo"],
        alert_lifecycle=deps["alert_lifecycle"],
        insight_repository=_FakeInsightRepository(),
        report_repository=_FakeReportRepository(),
        capacity_forecast_repository=_FakeCapacityForecastRepository(),
        frontend_base_url=FRONTEND_URL,
        bff_public_url=BFF_URL,
        cookie_secure=False,
    )
    return TestClient(app)


class TestUsersRoles:
    def test_requires_platform_auth(self, app_client: TestClient) -> None:
        assert app_client.get("/admin/platform-users").status_code == 401

    def test_create_and_list(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        cookies = {"voiceos_session": _platform_token(session_codec)}
        created = app_client.post(
            "/admin/platform-users",
            json={"email": "new@voiceos.ai", "name": "New Admin", "platform_role": "PLATFORM_SUPPORT"},
            cookies=cookies,
        )
        assert created.status_code == 201
        listed = app_client.get("/admin/platform-users", cookies=cookies)
        assert any(u["email"] == "new@voiceos.ai" for u in listed.json())

    def test_support_role_cannot_write(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        cookies = {"voiceos_session": _platform_token(session_codec, role="PLATFORM_SUPPORT")}
        response = app_client.post(
            "/admin/platform-users", json={"email": "x@x.com", "name": "X", "platform_role": "PLATFORM_SUPPORT"}, cookies=cookies
        )
        assert response.status_code == 403


class TestAdminAuditLogs:
    def test_lists_tenant_events(self, app_client: TestClient, session_codec: WebSessionCodec, deps: dict[str, Any]) -> None:
        from src.libs.audit.event import AuditEvent

        deps["admin_audit_repo"].events[TENANT_A] = [
            AuditEvent(
                audit_id="a1", tenant_id=TENANT_A, actor_id="u1", action="admin_portal.suspend",
                resource_type="AdminAPI", resource_id="r1", outcome="SUCCESS", recorded_at=datetime.now(UTC),
            )
        ]
        response = app_client.get(f"/admin/clients/{TENANT_A}/audit-logs", cookies={"voiceos_session": _platform_token(session_codec)})
        assert response.status_code == 200
        assert len(response.json()) == 1


class TestCRM:
    def test_list_and_create(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        cookies = {"voiceos_session": _tenant_token(session_codec)}
        created = app_client.post("/crm/customers", json={"crm_id": "c-1", "name": "Priya"}, cookies=cookies)
        assert created.status_code == 201
        listed = app_client.get("/crm/customers", cookies=cookies)
        assert len(listed.json()) == 1

    def test_manager_cannot_create(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        cookies = {"voiceos_session": _tenant_token(session_codec, role="MANAGER")}
        response = app_client.post("/crm/customers", json={"crm_id": "c-1", "name": "Priya"}, cookies=cookies)
        assert response.status_code == 403


class TestCollections:
    def test_list_and_resolve(self, app_client: TestClient, session_codec: WebSessionCodec, deps: dict[str, Any]) -> None:
        deps["escalation_repo"].store["e-1"] = EscalationRecord(
            escalation_id="e-1", tenant_id=TenantId(TENANT_A), call_id=CallId("call-1"), customer_id=CustomerId("cust-1"),
            reason="ABUSE_DETECTED", escalated_to="LEGAL", escalated_at=datetime.now(UTC),
        )
        cookies = {"voiceos_session": _tenant_token(session_codec)}
        listed = app_client.get("/collections/escalations", cookies=cookies)
        assert len(listed.json()) == 1
        resolved = app_client.post("/collections/escalations/e-1/resolve", json={"resolution_notes": "handled"}, cookies=cookies)
        assert resolved.status_code == 200


class TestBilling:
    def test_create_then_get_subscription(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        cookies = {"voiceos_session": _platform_token(session_codec)}
        created = app_client.post(f"/admin/clients/{TENANT_A}/subscription", json={"tier": "GROWTH"}, cookies=cookies)
        assert created.status_code == 201
        assert created.json()["tier"] == "GROWTH"
        fetched = app_client.get(f"/admin/clients/{TENANT_A}/subscription", cookies=cookies)
        assert fetched.status_code == 200

    def test_billing_ops_cannot_write_clients(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        cookies = {"voiceos_session": _platform_token(session_codec, role="PLATFORM_BILLING_OPS")}
        response = app_client.post(f"/admin/clients/{TENANT_A}/subscription", json={"tier": "GROWTH"}, cookies=cookies)
        assert response.status_code == 201  # PLATFORM_BILLING_OPS holds PERM_PLATFORM_WRITE_BILLING


class TestPlatformSettings:
    def test_set_and_list_flag(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        cookies = {"voiceos_session": _platform_token(session_codec)}
        response = app_client.post(
            "/admin/feature-flags/ops_intelligence_reasoning", json={"scope": "GLOBAL", "state": "ENABLED"}, cookies=cookies
        )
        assert response.status_code == 200
        listed = app_client.get("/admin/feature-flags", cookies=cookies)
        flag = next(f for f in listed.json() if f["flag_name"] == "ops_intelligence_reasoning")
        assert flag["targeting_rows"][0]["state"] == "ENABLED"


class TestInfrastructure:
    def test_empty_fleet_is_healthy_by_default(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        cookies = {"voiceos_session": _platform_token(session_codec)}
        response = app_client.get("/admin/infrastructure/gpu-fleet", cookies=cookies)
        assert response.status_code == 200
        assert response.json()["fleet_health_score"] == 1.0


class TestAlertsCenter:
    def test_acknowledge_flow(self, app_client: TestClient, session_codec: WebSessionCodec, deps: dict[str, Any]) -> None:
        alert = AlertRecord(
            alert_id="al-1", tenant_id=None, source=AlertSource.ALERTMANAGER, fingerprint="fp-1",
            severity=Severity.WARNING, status=AlertStatus.FIRING, fired_at=datetime.now(UTC), labels={}, annotations={},
        )
        deps["alert_repo"].store["al-1"] = alert
        cookies = {"voiceos_session": _platform_token(session_codec)}
        listed = app_client.get("/admin/alerts", cookies=cookies)
        assert len(listed.json()) == 1
        acked = app_client.post("/admin/alerts/al-1/acknowledge", cookies=cookies)
        assert acked.status_code == 200
        assert acked.json()["status"] == "acknowledged"

    def test_support_cannot_acknowledge(self, app_client: TestClient, session_codec: WebSessionCodec, deps: dict[str, Any]) -> None:
        deps["alert_repo"].store["al-1"] = AlertRecord(
            alert_id="al-1", tenant_id=None, source=AlertSource.ALERTMANAGER, fingerprint="fp-1",
            severity=Severity.WARNING, status=AlertStatus.FIRING, fired_at=datetime.now(UTC), labels={}, annotations={},
        )
        cookies = {"voiceos_session": _platform_token(session_codec, role="PLATFORM_SUPPORT")}
        response = app_client.post("/admin/alerts/al-1/acknowledge", cookies=cookies)
        assert response.status_code == 403


class TestAIInsights:
    def test_disabled_by_default(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        cookies = {"voiceos_session": _platform_token(session_codec)}
        response = app_client.get("/admin/ai-insights", cookies=cookies)
        assert response.status_code == 200
        assert response.json() == {"reasoning_enabled": False, "insights": []}

    def test_enabled_returns_persisted_insights(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        cookies = {"voiceos_session": _platform_token(session_codec)}
        app_client.post("/admin/feature-flags/ops_intelligence_reasoning", json={"scope": "GLOBAL", "state": "ENABLED"}, cookies=cookies)
        response = app_client.get("/admin/ai-insights", cookies=cookies)
        assert response.json()["reasoning_enabled"] is True


class TestCapacityPlanning:
    def test_requires_resource_param(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        cookies = {"voiceos_session": _platform_token(session_codec)}
        response = app_client.get("/admin/capacity-forecasts", cookies=cookies)
        assert response.status_code == 422


class TestIncidentTimeline:
    def test_merges_alerts_and_insights(self, app_client: TestClient, session_codec: WebSessionCodec, deps: dict[str, Any]) -> None:
        deps["alert_repo"].store["al-1"] = AlertRecord(
            alert_id="al-1", tenant_id=None, source=AlertSource.ALERTMANAGER, fingerprint="fp-1",
            severity=Severity.CRITICAL, status=AlertStatus.FIRING, fired_at=datetime.now(UTC), labels={}, annotations={},
        )
        cookies = {"voiceos_session": _platform_token(session_codec)}
        response = app_client.get("/admin/incident-timeline", cookies=cookies)
        assert response.status_code == 200
        assert any(e["type"] == "alert" for e in response.json())
