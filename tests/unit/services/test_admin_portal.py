"""Unit tests for the Administration Portal (Sprint-025, V5 Ch13).

Runs fully in-process via Starlette's ``TestClient`` -- no live Postgres/
Redis/network required (Phase 1). Every wrapped service is a small fake
double (mirroring the ``_Fake*Repository`` precedent used throughout this
suite), so these exercise ``AdminAPI``'s routing/auth/RBAC wiring, not the
already-unit-tested business logic of ``TenantService``/``CampaignService``/etc.

Required named test (Sprint-025.md):
    test_admin_api_agent_role_forbidden -- AGENT JWT -> admin endpoint -> 403
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from starlette.testclient import TestClient

from src.libs.audit.event import AuditEvent
from src.libs.contracts.models.billing import UsageType
from src.libs.contracts.models.campaign import AudienceCriteria, Campaign, CampaignStatus, RetryPolicy
from src.libs.contracts.models.integration import APIKeyRecord
from src.libs.contracts.models.tenant import Tenant
from src.libs.contracts.primitives import CampaignId, TenantId
from src.services.admin_portal.ai_config_admin import AIConfigAdminController
from src.services.admin_portal.api import create_admin_api
from src.services.admin_portal.api_key_admin import APIKeyAdminController
from src.services.admin_portal.audit_admin import AdminAuditViewNotConfiguredError, AuditAdminController
from src.services.admin_portal.billing_admin import BillingAdminController
from src.services.admin_portal.campaign_admin import CampaignAdminController, CampaignApprovalDeniedError
from src.services.admin_portal.tenant_admin import TenantAdminController
from src.services.admin_portal.user_admin import UserAdminController
from src.services.api_platform.api_key_lifecycle import APIKeyLifecycleService, APIKeyNotFoundError
from src.services.auth.jwt_validator import JWTValidator, issue_test_token
from src.services.auth.service import AuthService
from src.services.policy_engine.service import PolicyEngineService
from src.services.reporting.exporter import ExportService

_TENANT = TenantId("tenant-a")
_OTHER_TENANT = TenantId("tenant-b")


def _rsa_keypair() -> tuple[RSAPrivateKey, RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


class _FakeTenantService:
    def __init__(self) -> None:
        self._tenant = Tenant(
            tenant_id=_TENANT,
            slug="tenant-a",
            display_name="Tenant A",
            subscription_tier="GROWTH",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    def get(self, tenant_id: TenantId) -> Tenant | None:
        return self._tenant if tenant_id == _TENANT else None

    def suspend(self, tenant_id: TenantId, actor_id: str) -> Tenant:
        return self._tenant

    def reactivate(self, tenant_id: TenantId, actor_id: str) -> Tenant:
        return self._tenant


class _FakeUserService:
    def list_users(self, tenant_id: TenantId) -> tuple[object, ...]:
        return ()


class _FakeCampaignService:
    def __init__(self, status: CampaignStatus = CampaignStatus.REVIEW) -> None:
        self._campaign = Campaign(
            campaign_id=CampaignId("campaign-1"),
            tenant_id=_TENANT,
            name="Campaign A",
            status=status,
            audience_criteria=AudienceCriteria(),
            retry_policy=RetryPolicy(),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            created_by="system",
        )

    def find_active(self, tenant_id: TenantId) -> tuple[Campaign, ...]:
        return ()

    def get(self, tenant_id: TenantId, campaign_id: CampaignId) -> Campaign | None:
        return self._campaign if campaign_id == self._campaign.campaign_id else None

    def approve(self, tenant_id: TenantId, campaign_id: CampaignId, approved_by: str) -> Campaign:
        return self._campaign.model_copy(update={"status": CampaignStatus.APPROVED})


class _FakeSSOIntegration:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def configure(self, tenant_id: str, provider: str, config: dict[str, Any]) -> None:
        self.calls.append((tenant_id, provider, config))


class _FakeUsageAggregator:
    def aggregate_period(
        self, tenant_id: TenantId, period_start: datetime, period_end: datetime
    ) -> dict[UsageType, int]:
        return {UsageType.CALL_MINUTE: 42}


class _FakeBillingService:
    def get_subscription(self, tenant_id: TenantId) -> object | None:
        return None


class _FakeInvoiceRepository:
    def list_invoices(self, tenant_id: TenantId) -> tuple[object, ...]:
        return ()


class _FakeAuditSearch:
    def by_resource(self, tenant_id: TenantId, resource_type: str, resource_id: str) -> list[AuditEvent]:
        return []

    def in_range(self, tenant_id: TenantId, start: object = None, end: object = None) -> list[AuditEvent]:
        return [
            AuditEvent(
                audit_id="audit-1",
                tenant_id=str(_TENANT),
                actor_id="user-1",
                action="tenant.suspend",
                resource_type="Tenant",
                resource_id=str(_TENANT),
                outcome="SUCCESS",
                recorded_at=datetime.now(UTC),
            )
        ]


class _FakeAdminAuditViewRepository:
    def list_for_tenant(self, tenant_id: TenantId) -> tuple[AuditEvent, ...]:
        return (
            AuditEvent(
                audit_id="audit-2",
                tenant_id=str(_TENANT),
                actor_id="admin-1",
                action="admin_portal.mutation",
                resource_type="AdminAPI",
                resource_id="/admin/v1/campaigns/campaign-1/approve",
                outcome="SUCCESS",
                recorded_at=datetime.now(UTC),
            ),
        )


class _FakePromptVersioning:
    def edit(self, tenant_id: TenantId, prompt_version_id: str, template: str) -> object:
        raise NotImplementedError


class _FakeAIConfigService:
    prompt_versioning = _FakePromptVersioning()

    def create_prompt_version(self, tenant_id: TenantId, name: str, template: str, language: str = "en") -> object:
        raise NotImplementedError

    def publish_prompt_version(self, tenant_id: TenantId, prompt_version_id: str) -> object:
        raise NotImplementedError

    def configure_model(self, tenant_id: TenantId, model_config: object, campaign_id: str | None = None) -> None:
        raise NotImplementedError


def _client(
    *,
    campaign_status: CampaignStatus = CampaignStatus.REVIEW,
    policy_engine: PolicyEngineService | None = None,
    sso: _FakeSSOIntegration | None = None,
    export_service: ExportService | None = None,
    usage_aggregator: _FakeUsageAggregator | None = None,
    admin_audit_view_repository: _FakeAdminAuditViewRepository | None = None,
    api_key_admin: APIKeyAdminController | None = None,
) -> tuple[TestClient, RSAPrivateKey]:
    private_key, public_key = _rsa_keypair()
    auth_service = AuthService(jwt_validator=JWTValidator(public_key))
    app = create_admin_api(
        auth_service=auth_service,
        tenant_admin=TenantAdminController(_FakeTenantService()),  # type: ignore[arg-type]
        user_admin=UserAdminController(_FakeUserService(), sso),  # type: ignore[arg-type]
        campaign_admin=CampaignAdminController(_FakeCampaignService(campaign_status), policy_engine),  # type: ignore[arg-type]
        billing_admin=BillingAdminController(
            _FakeBillingService(),  # type: ignore[arg-type]
            _FakeInvoiceRepository(),  # type: ignore[arg-type]
            usage_aggregator,  # type: ignore[arg-type]
        ),
        audit_admin=AuditAdminController(
            _FakeAuditSearch(),  # type: ignore[arg-type]
            export_service,
            admin_audit_view_repository,  # type: ignore[arg-type]
        ),
        ai_config_admin=AIConfigAdminController(_FakeAIConfigService()),  # type: ignore[arg-type]
        api_key_admin=api_key_admin,
    )
    return TestClient(app), private_key


def _token(private_key: RSAPrivateKey, role: str) -> str:
    return issue_test_token(private_key, subject="user-1", tenant_id=str(_TENANT), role=role)


class TestAdminAPIAuthorization:
    def test_admin_api_agent_role_forbidden(self) -> None:
        client, private_key = _client()
        token = _token(private_key, "AGENT")

        response = client.get("/admin/v1/tenants", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 403

    def test_admin_api_admin_role_permitted(self) -> None:
        client, private_key = _client()
        token = _token(private_key, "ADMIN")

        response = client.get("/admin/v1/tenants", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert response.json()[0]["tenant_id"] == str(_TENANT)

    def test_admin_api_supervisor_role_permitted(self) -> None:
        client, private_key = _client()
        token = _token(private_key, "SUPERVISOR")

        response = client.get("/admin/v1/campaigns", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200

    def test_admin_api_unauthenticated_returns_401(self) -> None:
        client, _private_key = _client()

        response = client.get("/admin/v1/tenants")

        assert response.status_code == 401

    def test_approve_campaign(self) -> None:
        client, private_key = _client()
        token = _token(private_key, "ADMIN")

        response = client.post("/admin/v1/campaigns/campaign-1/approve", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert response.json()["status"] == "APPROVED"


class TestCampaignApprovalPolicyGate:
    """PolicyEngine-validated campaign approval (Sprint-025.md: "is PolicyEngine validated
    where applicable"), via ``AdminPolicyPack.CAMPAIGN_APPROVAL_REQUIRES_REVIEW``."""

    def test_approve_denied_when_campaign_not_reviewed(self) -> None:
        client, private_key = _client(campaign_status=CampaignStatus.DRAFT, policy_engine=PolicyEngineService.create())
        token = _token(private_key, "ADMIN")

        response = client.post("/admin/v1/campaigns/campaign-1/approve", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 403

    def test_approve_permitted_when_campaign_reviewed(self) -> None:
        client, private_key = _client(campaign_status=CampaignStatus.REVIEW, policy_engine=PolicyEngineService.create())
        token = _token(private_key, "ADMIN")

        response = client.post("/admin/v1/campaigns/campaign-1/approve", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert response.json()["status"] == "APPROVED"

    def test_controller_raises_directly_without_http(self) -> None:
        controller = CampaignAdminController(
            _FakeCampaignService(CampaignStatus.DRAFT),  # type: ignore[arg-type]
            PolicyEngineService.create(),
        )

        try:
            controller.approve(_TENANT, CampaignId("campaign-1"), "admin-1")
            raised = False
        except CampaignApprovalDeniedError:
            raised = True

        assert raised


class TestSSOConfiguration:
    def test_configure_sso_success(self) -> None:
        client, private_key = _client(sso=_FakeSSOIntegration())
        token = _token(private_key, "ADMIN")

        response = client.post(
            "/admin/v1/users/sso",
            json={"provider": "OIDC", "config": {"issuer": "https://okta.example.com"}},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 202

    def test_configure_sso_without_backend_returns_501(self) -> None:
        client, private_key = _client()
        token = _token(private_key, "ADMIN")

        response = client.post(
            "/admin/v1/users/sso",
            json={"provider": "OIDC", "config": {}},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 501

    def test_configure_sso_invalid_provider_returns_422(self) -> None:
        """A vendor name (e.g. "okta") is not a valid ``provider`` value -- only the
        protocol (NONE/SAML/OIDC) is, matching the ``sso_config`` CHECK constraint."""
        client, private_key = _client(sso=_FakeSSOIntegration())
        token = _token(private_key, "ADMIN")

        response = client.post(
            "/admin/v1/users/sso",
            json={"provider": "okta", "config": {}},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 422


class TestComplianceReporting:
    def test_compliance_report_route(self) -> None:
        client, private_key = _client()
        token = _token(private_key, "ADMIN")

        response = client.get("/admin/v1/audit/compliance-report", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        body = response.json()
        assert "Compliance Audit" in body["title"]
        assert body["rows"][0][2] == "tenant.suspend"

    def test_compliance_report_service_method(self) -> None:
        controller = AuditAdminController(_FakeAuditSearch())  # type: ignore[arg-type]

        report = controller.compliance_report(_TENANT, "Tenant A")

        assert report.columns == ("Recorded At", "Actor", "Action", "Resource", "Outcome")
        assert len(report.rows) == 1

    def test_export_compliance_report_delegates_to_exporter(self) -> None:
        controller = AuditAdminController(_FakeAuditSearch(), ExportService())  # type: ignore[arg-type]

        csv_bytes = controller.export_compliance_report(_TENANT, "Tenant A", "csv")

        assert b"tenant.suspend" in csv_bytes


class TestUsageReporting:
    def test_usage_summary_route(self) -> None:
        client, private_key = _client(usage_aggregator=_FakeUsageAggregator())
        token = _token(private_key, "ADMIN")

        response = client.get(
            "/admin/v1/billing/usage",
            params={"period_start": "2026-07-01T00:00:00+00:00", "period_end": "2026-07-31T00:00:00+00:00"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.json()["CALL_MINUTE"] == 42

    def test_usage_summary_without_backend_returns_501(self) -> None:
        client, private_key = _client()
        token = _token(private_key, "ADMIN")

        response = client.get(
            "/admin/v1/billing/usage",
            params={"period_start": "2026-07-01T00:00:00+00:00", "period_end": "2026-07-31T00:00:00+00:00"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 501

    def test_usage_summary_service_method(self) -> None:
        controller = BillingAdminController(
            _FakeBillingService(),  # type: ignore[arg-type]
            _FakeInvoiceRepository(),  # type: ignore[arg-type]
            _FakeUsageAggregator(),  # type: ignore[arg-type]
        )

        summary = controller.usage_summary(_TENANT, datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 31, tzinfo=UTC))

        assert summary[UsageType.CALL_MINUTE] == 42


class TestAdminAuditViews:
    """Sprint-025 Part-3: ``admin_audit_views`` -- Admin-Portal-scoped audit query surface."""

    def test_list_admin_actions_route(self) -> None:
        client, private_key = _client(admin_audit_view_repository=_FakeAdminAuditViewRepository())
        token = _token(private_key, "ADMIN")

        response = client.get("/admin/v1/audit/admin-actions", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        body = response.json()
        assert body[0]["action"] == "admin_portal.mutation"

    def test_list_admin_actions_without_backend_returns_501(self) -> None:
        client, private_key = _client()
        token = _token(private_key, "ADMIN")

        response = client.get("/admin/v1/audit/admin-actions", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 501

    def test_list_admin_actions_service_method_raises_when_unconfigured(self) -> None:
        controller = AuditAdminController(_FakeAuditSearch())  # type: ignore[arg-type]

        with pytest.raises(AdminAuditViewNotConfiguredError):
            controller.list_admin_actions(_TENANT)

    def test_list_admin_actions_service_method(self) -> None:
        controller = AuditAdminController(
            _FakeAuditSearch(),  # type: ignore[arg-type]
            admin_audit_view_repository=_FakeAdminAuditViewRepository(),  # type: ignore[arg-type]
        )

        events = controller.list_admin_actions(_TENANT)

        assert len(events) == 1
        assert events[0].resource_type == "AdminAPI"


class _FakeAPIKeyRepositoryForLifecycle:
    def __init__(self) -> None:
        self._store: dict[str, APIKeyRecord] = {}

    def create(self, record: APIKeyRecord) -> APIKeyRecord:
        self._store[record.api_key_id] = record
        return record

    def list_for_tenant(self, tenant_id: TenantId) -> tuple[APIKeyRecord, ...]:
        return tuple(r for r in self._store.values() if r.tenant_id == tenant_id)

    def revoke(self, tenant_id: TenantId, api_key_id: str, revoked_at: object) -> int:
        record = self._store.get(api_key_id)
        if record is None or record.tenant_id != tenant_id:
            return 0
        self._store[api_key_id] = record.model_copy(update={"is_revoked": True})
        return 1

    def rotate(self, tenant_id: TenantId, api_key_id: str, new_key_hash: str) -> int:
        record = self._store.get(api_key_id)
        if record is None or record.tenant_id != tenant_id:
            return 0
        self._store[api_key_id] = record.model_copy(update={"key_hash": new_key_hash})
        return 1


class TestAPIKeyLifecycleService:
    """Sprint-025 Part-3: API key issuance, secure hashing, rotation, revocation,
    expiration, tenant ownership, audit logging, plan association."""

    def test_issue_returns_raw_key_and_persists_only_hash(self) -> None:
        repo = _FakeAPIKeyRepositoryForLifecycle()
        service = APIKeyLifecycleService(repo)

        raw_key, record = service.issue(_TENANT, plan_tier="GROWTH", issued_by="admin-1")

        assert raw_key
        assert record.key_hash != raw_key
        assert record.plan_tier == "GROWTH"
        assert record.tenant_id == _TENANT

    def test_rotate_changes_hash_and_returns_new_raw_key(self) -> None:
        repo = _FakeAPIKeyRepositoryForLifecycle()
        service = APIKeyLifecycleService(repo)
        _old_raw, record = service.issue(_TENANT, issued_by="admin-1")

        new_raw_key = service.rotate(_TENANT, record.api_key_id, "admin-1")

        rotated = repo.list_for_tenant(_TENANT)[0]
        assert rotated.key_hash != record.key_hash
        assert new_raw_key != _old_raw

    def test_rotate_missing_key_raises(self) -> None:
        repo = _FakeAPIKeyRepositoryForLifecycle()
        service = APIKeyLifecycleService(repo)

        with pytest.raises(APIKeyNotFoundError):
            service.rotate(_TENANT, "no-such-key", "admin-1")

    def test_revoke_missing_key_raises(self) -> None:
        repo = _FakeAPIKeyRepositoryForLifecycle()
        service = APIKeyLifecycleService(repo)

        with pytest.raises(APIKeyNotFoundError):
            service.revoke(_TENANT, "no-such-key", "admin-1")

    def test_revoke_marks_key_revoked(self) -> None:
        repo = _FakeAPIKeyRepositoryForLifecycle()
        service = APIKeyLifecycleService(repo)
        _raw, record = service.issue(_TENANT, issued_by="admin-1")

        service.revoke(_TENANT, record.api_key_id, "admin-1")

        assert repo.list_for_tenant(_TENANT)[0].is_revoked is True

    def test_tenant_isolation_rotate_cross_tenant_raises(self) -> None:
        repo = _FakeAPIKeyRepositoryForLifecycle()
        service = APIKeyLifecycleService(repo)
        _raw, record = service.issue(_TENANT, issued_by="admin-1")

        with pytest.raises(APIKeyNotFoundError):
            service.rotate(_OTHER_TENANT, record.api_key_id, "admin-1")


class TestAPIKeyAdminRoutes:
    def test_issue_rotate_revoke_via_admin_api(self) -> None:
        lifecycle = APIKeyLifecycleService(_FakeAPIKeyRepositoryForLifecycle())
        controller = APIKeyAdminController(lifecycle)
        client, private_key = _client(api_key_admin=controller)
        token = _token(private_key, "ADMIN")
        headers = {"Authorization": f"Bearer {token}"}

        issue_response = client.post("/admin/v1/api-keys", json={"plan_tier": "GROWTH"}, headers=headers)
        assert issue_response.status_code == 201
        body = issue_response.json()
        assert body["raw_key"]
        api_key_id = body["api_key_id"]

        list_response = client.get("/admin/v1/api-keys", headers=headers)
        assert list_response.status_code == 200
        assert list_response.json()[0]["key_hash"] is None

        rotate_response = client.post(f"/admin/v1/api-keys/{api_key_id}/rotate", headers=headers)
        assert rotate_response.status_code == 200
        assert rotate_response.json()["raw_key"] != body["raw_key"]

        revoke_response = client.post(f"/admin/v1/api-keys/{api_key_id}/revoke", headers=headers)
        assert revoke_response.status_code == 200

    def test_api_key_routes_without_backend_return_501(self) -> None:
        client, private_key = _client()
        token = _token(private_key, "ADMIN")
        headers = {"Authorization": f"Bearer {token}"}

        assert client.get("/admin/v1/api-keys", headers=headers).status_code == 501
        assert client.post("/admin/v1/api-keys", json={}, headers=headers).status_code == 501
        assert client.post("/admin/v1/api-keys/x/rotate", headers=headers).status_code == 501
        assert client.post("/admin/v1/api-keys/x/revoke", headers=headers).status_code == 501

    def test_rotate_missing_key_returns_404(self) -> None:
        lifecycle = APIKeyLifecycleService(_FakeAPIKeyRepositoryForLifecycle())
        controller = APIKeyAdminController(lifecycle)
        client, private_key = _client(api_key_admin=controller)
        token = _token(private_key, "ADMIN")

        response = client.post("/admin/v1/api-keys/no-such-key/rotate", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 404
