"""Unit/integration tests for the Public API (Sprint-025, V5 Ch16).

Runs fully in-process via Starlette's ``TestClient`` (httpx-based, same
precedent as ``tests/unit/libs/test_health.py``) -- no live Postgres/Redis
/network required (Phase 1); Redis-backed rate limiting uses the shared
``FakeRedisClient`` fixture (``tests/fixtures/redis.py``).

Required named tests (Sprint-025.md):
    test_public_api_authenticated -- valid API key -> 200; invalid -> 401
    test_openapi_spec_validates -- spec file passes structural validation
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from scripts.validate_openapi import validate as validate_openapi_spec
from starlette.testclient import TestClient

from src.libs.contracts.models.billing import Invoice, InvoiceStatus, SubscriptionTier
from src.libs.contracts.models.campaign import Campaign, CampaignStatus
from src.libs.contracts.models.customer import Customer
from src.libs.contracts.models.integration import APIKeyRecord, APIRateLimitConfig, WebhookRegistration
from src.libs.contracts.primitives import CampaignId, CustomerId, TenantId
from src.libs.redis_client.rate_limiter import RateLimiter
from src.services.api_platform.api import create_public_api
from src.services.api_platform.openapi import get_openapi_schema
from src.services.auth.api_key_validator import APIKeyValidator
from src.services.auth.service import AuthService
from src.services.policy_engine.decision import PolicyDecision, PolicyOutcome
from src.services.policy_engine.service import PolicyEngineService
from tests.fixtures.redis import FakeRedisClient

_TENANT = TenantId("tenant-a")
_RAW_API_KEY = "test-api-key-12345"


class _FakeCustomerRepository:
    def get(self, tenant_id: TenantId, customer_id: CustomerId) -> Customer | None:
        if str(customer_id) != "cust-1":
            return None
        return Customer(
            customer_id=customer_id,
            tenant_id=tenant_id,
            crm_id="crm-1",
            name="Test Customer",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


class _FakeCallDispositionRepository:
    def get_by_call_id(self, tenant_id: TenantId, call_id: str) -> object | None:
        return None


class _FakeCampaignService:
    def __init__(self) -> None:
        self._campaigns: dict[str, Campaign] = {}

    def create(
        self,
        tenant_id: TenantId,
        name: str,
        audience_criteria: object,
        retry_policy: object,
        created_by: str,
        **kwargs: object,
    ) -> Campaign:
        campaign = Campaign(
            campaign_id=CampaignId("campaign-1"),
            tenant_id=tenant_id,
            name=name,
            audience_criteria=audience_criteria,  # type: ignore[arg-type]
            retry_policy=retry_policy,  # type: ignore[arg-type]
            status=CampaignStatus.DRAFT,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            created_by=created_by,
        )
        self._campaigns[campaign.campaign_id] = campaign
        return campaign

    def get(self, tenant_id: TenantId, campaign_id: CampaignId) -> Campaign | None:
        return self._campaigns.get(campaign_id)


class _FakeCampaignAnalytics:
    def ptp_rate(self, tenant_id: TenantId, campaign_id: CampaignId) -> float:
        return 0.3

    def results_for(self, tenant_id: TenantId, campaign_id: CampaignId) -> tuple[object, ...]:
        return ()


class _FakeWebhookService:
    def register_endpoint(
        self, tenant_id: TenantId, url: str, event_types: tuple[str, ...], secret: str | None = None
    ) -> WebhookRegistration:
        return WebhookRegistration(
            webhook_id="wh-1",
            tenant_id=tenant_id,
            url=url,
            secret=secret or "generated-secret",
            event_types=event_types,
            created_at=datetime.now(UTC),
        )


class _FakeInvoiceRepository:
    def list_invoices(self, tenant_id: TenantId) -> tuple[Invoice, ...]:
        return (
            Invoice(
                invoice_id="inv-1",
                tenant_id=tenant_id,
                billing_period_start=datetime(2026, 7, 1, tzinfo=UTC),
                billing_period_end=datetime(2026, 8, 1, tzinfo=UTC),
                subtotal_minor=10000,
                tax_minor=1800,
                total_minor=11800,
                currency="INR",
                status=InvoiceStatus.ISSUED,
                due_date=datetime(2026, 8, 15, tzinfo=UTC),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
        )


class _FakeDenyingPolicyEngine:
    """A ``PolicyEngineService``-shaped fake whose ``check_entitlement`` always denies."""

    def check_entitlement(self, **_kwargs: Any) -> PolicyDecision:
        return PolicyDecision(outcome=PolicyOutcome.DENY, reason="trial expired")


class _FakeAPIKeyRepositoryPublic:
    """A ``PersistedAPIKeyRecord``-shaped repository fake, so a resolved key carries a
    real ``api_key_id`` (Sprint-025 Part-3: usage logging needs the FK-valid id, not
    the in-memory key store's digest-only identity)."""

    def __init__(self, record: APIKeyRecord) -> None:
        self._record = record

    def find_by_hash(self, key_hash: str) -> APIKeyRecord | None:
        return self._record if key_hash == self._record.key_hash else None


class _FakeRateLimitRepository:
    def __init__(self, config: APIRateLimitConfig | None) -> None:
        self._config = config

    def get(self, tier: str) -> APIRateLimitConfig | None:
        return self._config


class _FakeUsageRepository:
    def __init__(self) -> None:
        self.recorded: list[tuple[str, str, str, int]] = []

    def record(self, tenant_id: TenantId, api_key_id: str, route: str, status_code: int) -> None:
        self.recorded.append((str(tenant_id), api_key_id, route, status_code))


def _client(
    policy_engine: PolicyEngineService | _FakeDenyingPolicyEngine | None = None,
    *,
    rate_limiter: RateLimiter | None = None,
    rate_limit_repository: _FakeRateLimitRepository | None = None,
    usage_repository: _FakeUsageRepository | None = None,
    repository_backed_key: APIKeyRecord | None = None,
) -> TestClient:
    key_validator = APIKeyValidator(
        repository=_FakeAPIKeyRepositoryPublic(repository_backed_key) if repository_backed_key is not None else None
    )
    if repository_backed_key is None:
        key_validator.register_key(_RAW_API_KEY, tenant_id=str(_TENANT), role="", scopes=())
    auth_service = AuthService(api_key_validator=key_validator)

    app = create_public_api(
        auth_service=auth_service,
        rate_limiter=rate_limiter if rate_limiter is not None else RateLimiter(FakeRedisClient()),
        tier_lookup=lambda _tenant_id: SubscriptionTier.GROWTH,
        customer_repository=_FakeCustomerRepository(),
        call_disposition_repository=_FakeCallDispositionRepository(),
        campaign_service=_FakeCampaignService(),
        campaign_analytics=_FakeCampaignAnalytics(),
        webhook_service=_FakeWebhookService(),
        invoice_repository=_FakeInvoiceRepository(),
        policy_engine=policy_engine,  # type: ignore[arg-type]
        rate_limit_repository=rate_limit_repository,
        usage_repository=usage_repository,
    )
    return TestClient(app)


class TestPublicAPIAuthentication:
    def test_public_api_authenticated(self) -> None:
        client = _client()

        ok_response = client.get("/customers/cust-1", headers={"X-API-Key": _RAW_API_KEY})
        assert ok_response.status_code == 200
        assert ok_response.json()["name"] == "Test Customer"

        unauthenticated_response = client.get("/customers/cust-1")
        assert unauthenticated_response.status_code == 401

        invalid_key_response = client.get("/customers/cust-1", headers={"X-API-Key": "not-a-real-key"})
        assert invalid_key_response.status_code == 401

    def test_customer_not_found_returns_404(self) -> None:
        client = _client()
        response = client.get("/customers/does-not-exist", headers={"X-API-Key": _RAW_API_KEY})
        assert response.status_code == 404

    def test_create_campaign_and_fetch_analytics(self) -> None:
        client = _client()
        headers = {"X-API-Key": _RAW_API_KEY}

        create_response = client.post("/campaigns", json={"name": "New Campaign"}, headers=headers)
        assert create_response.status_code == 201

        analytics_response = client.get("/campaigns/campaign-1/analytics", headers=headers)
        assert analytics_response.status_code == 200
        assert analytics_response.json()["ptp_rate"] == 0.3

    def test_register_webhook_rejects_unknown_event_type(self) -> None:
        client = _client()
        response = client.post(
            "/webhooks",
            json={"url": "https://example.com/hook", "event_types": ["not.a.real.event"]},
            headers={"X-API-Key": _RAW_API_KEY},
        )
        assert response.status_code == 422

    def test_list_invoices(self) -> None:
        client = _client()
        response = client.get("/invoices", headers={"X-API-Key": _RAW_API_KEY})
        assert response.status_code == 200
        assert response.json()[0]["invoice_id"] == "inv-1"

    def test_openapi_json_unauthenticated(self) -> None:
        client = _client()
        response = client.get("/openapi.json")
        assert response.status_code == 200
        assert response.json()["openapi"].startswith("3.1")

    def test_error_envelope_is_uniform(self) -> None:
        client = _client()
        response = client.get("/customers/does-not-exist", headers={"X-API-Key": _RAW_API_KEY})
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "NOT_FOUND"
        assert body["error"]["message"]

    def test_malformed_json_payload_rejected(self) -> None:
        client = _client()
        response = client.post(
            "/campaigns",
            content=b"{not valid json",
            headers={"X-API-Key": _RAW_API_KEY, "Content-Type": "application/json"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "MALFORMED_PAYLOAD"

    def test_register_webhook_missing_url_rejected(self) -> None:
        client = _client()
        response = client.post(
            "/webhooks",
            json={"event_types": ["ptp.created"]},
            headers={"X-API-Key": _RAW_API_KEY},
        )
        assert response.status_code == 422


class TestPublicAPIPolicyEngineEntitlement:
    """PolicyEngine-integrated entitlement gate on the rate-limit middleware
    (Sprint-025.md: "PolicyEngine integration" for Public API rate limiting)."""

    def test_permit_by_default_does_not_block_requests(self) -> None:
        client = _client(policy_engine=PolicyEngineService.create())
        response = client.get("/customers/cust-1", headers={"X-API-Key": _RAW_API_KEY})
        assert response.status_code == 200

    def test_entitlement_denial_returns_403(self) -> None:
        client = _client(policy_engine=_FakeDenyingPolicyEngine())
        response = client.get("/customers/cust-1", headers={"X-API-Key": _RAW_API_KEY})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "ENTITLEMENT_DENIED"


class TestPublicAPIRateLimitAndUsage:
    """Sprint-025 Part-3: persisted ``api_rate_limits`` (billing-plan-determined
    capabilities), dual-window burst handling, and ``api_key_usage`` logging."""

    def test_persisted_rate_limit_overrides_hardcoded_default(self) -> None:
        """A persisted config with rps=1 should reject the 2nd request in the same second,
        even though the hardcoded GROWTH default (100) would have allowed it."""
        rate_limiter = RateLimiter(FakeRedisClient())
        config = APIRateLimitConfig(
            tier="GROWTH", requests_per_second=1, burst_capacity=1, updated_at=datetime.now(UTC)
        )
        client = _client(rate_limiter=rate_limiter, rate_limit_repository=_FakeRateLimitRepository(config))

        first = client.get("/customers/cust-1", headers={"X-API-Key": _RAW_API_KEY})
        second = client.get("/customers/cust-1", headers={"X-API-Key": _RAW_API_KEY})

        assert first.status_code == 200
        assert second.status_code == 429
        assert second.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"

    def test_falls_back_to_hardcoded_default_when_repository_has_no_row(self) -> None:
        client = _client(rate_limit_repository=_FakeRateLimitRepository(None))
        response = client.get("/customers/cust-1", headers={"X-API-Key": _RAW_API_KEY})
        assert response.status_code == 200

    def test_usage_logged_for_repository_backed_api_key(self) -> None:
        usage_repo = _FakeUsageRepository()
        record = APIKeyRecord(
            api_key_id="key-1",
            tenant_id=_TENANT,
            key_hash=APIKeyValidator.hash_key(_RAW_API_KEY),
            created_at=datetime.now(UTC),
        )
        client = _client(usage_repository=usage_repo, repository_backed_key=record)

        client.get("/customers/cust-1", headers={"X-API-Key": _RAW_API_KEY})

        assert len(usage_repo.recorded) == 1
        tenant_id, api_key_id, route, status_code = usage_repo.recorded[0]
        assert tenant_id == str(_TENANT)
        assert api_key_id == "key-1"
        assert route == "/customers/cust-1"
        assert status_code == 200

    def test_usage_not_logged_for_in_memory_only_key(self) -> None:
        """No api_key_id (in-memory `register_key` path) -> nothing to log."""
        usage_repo = _FakeUsageRepository()
        client = _client(usage_repository=usage_repo)

        client.get("/customers/cust-1", headers={"X-API-Key": _RAW_API_KEY})

        assert usage_repo.recorded == []


class TestOpenAPISpec:
    def test_openapi_spec_validates(self) -> None:
        schema = get_openapi_schema()
        errors = validate_openapi_spec(schema)
        assert errors == []

    def test_all_public_endpoints_present(self) -> None:
        schema = get_openapi_schema()
        expected_paths = {
            "/customers/{id}",
            "/calls/{id}",
            "/campaigns",
            "/campaigns/{id}/analytics",
            "/webhooks",
            "/invoices",
        }
        assert expected_paths.issubset(schema["paths"].keys())
