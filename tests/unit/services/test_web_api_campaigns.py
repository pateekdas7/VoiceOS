"""Unit tests for Client -> Campaigns CRUD + lifecycle routes (ADR-005 Sec 6.2).

All tests run fully in-process against a real Starlette TestClient with a
fake CampaignRepository -- no live Postgres/network required (Phase 1).
"""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from src.libs.contracts.models.campaign import ABTestVariant, Campaign, CampaignStatus
from src.services.authz.roles import ROLE_PERMISSIONS
from src.services.authz.roles import Role as AuthzRole
from src.services.campaign_management.service import CampaignService
from src.services.platform_admin.service import PlatformAdminService
from src.services.user_management.invitation import InvitationService
from src.services.user_management.service import UserService
from src.services.web_api.api import create_web_api
from src.services.web_api.session import WebSessionCodec

FRONTEND_URL = "https://app.voiceos.test"
BFF_URL = "https://bff.voiceos.test"
TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


class _FakeCampaignRepository:
    def __init__(self) -> None:
        self._store: dict[str, Campaign] = {}

    def create(self, campaign: Campaign) -> Campaign:
        self._store[campaign.campaign_id] = campaign
        return campaign

    def get(self, tenant_id: str, campaign_id: str) -> Campaign | None:
        campaign = self._store.get(campaign_id)
        if campaign is None or campaign.tenant_id != tenant_id:
            return None
        return campaign

    def find_active_for_tenant(self, tenant_id: str) -> tuple[Campaign, ...]:
        return tuple(
            c for c in self._store.values() if c.tenant_id == tenant_id and c.status == CampaignStatus.ACTIVE
        )

    def find_all_for_tenant(self, tenant_id: str) -> tuple[Campaign, ...]:
        return tuple(c for c in self._store.values() if c.tenant_id == tenant_id)

    def update_status(self, tenant_id: str, campaign_id: str, status: CampaignStatus) -> None:
        campaign = self._store[campaign_id]
        self._store[campaign_id] = campaign.model_copy(update={"status": status})

    def update_counts(self, tenant_id: str, campaign_id: str, target_call_count: int, completed_call_count: int) -> None:
        campaign = self._store[campaign_id]
        self._store[campaign_id] = campaign.model_copy(
            update={"target_call_count": target_call_count, "completed_call_count": completed_call_count}
        )

    def create_variant(self, campaign_id: str, variant: ABTestVariant) -> ABTestVariant:
        return variant


class _NoopPlatformUserRepository:
    def create(self, user: Any) -> Any:
        return user

    def get(self, platform_user_id: str) -> None:
        return None

    def find_by_email(self, email: str) -> None:
        return None

    def list_all(self) -> tuple[Any, ...]:
        return ()

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


@pytest.fixture
def session_codec() -> WebSessionCodec:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return WebSessionCodec(private_key, private_key.public_key())


@pytest.fixture
def campaign_repo() -> _FakeCampaignRepository:
    return _FakeCampaignRepository()


@pytest.fixture
def app_client(session_codec: WebSessionCodec, campaign_repo: _FakeCampaignRepository) -> TestClient:
    platform_admin = PlatformAdminService(_NoopPlatformUserRepository())
    invitation_service = InvitationService(_NoopInvitationRepository(), _NoopUserRepository())
    user_service = UserService(_NoopUserRepository(), invitation_service)
    campaign_service = CampaignService(campaign_repo)  # type: ignore[arg-type]

    app = create_web_api(
        session_codec=session_codec,
        google_oauth=_NoopGoogleOAuth(),
        platform_admin=platform_admin,
        user_service=user_service,
        campaign_service=campaign_service,
        frontend_base_url=FRONTEND_URL,
        bff_public_url=BFF_URL,
        cookie_secure=False,
    )
    return TestClient(app)


def _tenant_token(codec: WebSessionCodec, tenant_id: str = TENANT_A, role: str = "MANAGER") -> str:
    permissions = tuple(ROLE_PERMISSIONS.get(AuthzRole(role), frozenset()))
    return codec.encode(
        actor_kind="tenant", subject="u-1", role=role, permissions=permissions, email="u@tenant.com",
        tenant_id=tenant_id,
    )


def _platform_token(codec: WebSessionCodec) -> str:
    return codec.encode(actor_kind="platform", subject="pu-1", role="PLATFORM_ADMIN", email="a@voiceos.ai", tenant_id=None)


class TestListCampaigns:
    def test_requires_auth(self, app_client: TestClient) -> None:
        response = app_client.get("/campaigns")
        assert response.status_code == 401

    def test_rejects_platform_actor(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.get("/campaigns", cookies={"voiceos_session": _platform_token(session_codec)})
        assert response.status_code == 403

    def test_scoped_to_own_tenant_only(
        self, app_client: TestClient, session_codec: WebSessionCodec, campaign_repo: _FakeCampaignRepository
    ) -> None:
        campaign_repo._store["c-a"] = _campaign("c-a", TENANT_A, "Tenant A Campaign")
        campaign_repo._store["c-b"] = _campaign("c-b", TENANT_B, "Tenant B Campaign")

        response = app_client.get(
            "/campaigns", cookies={"voiceos_session": _tenant_token(session_codec, TENANT_A)}
        )
        assert response.status_code == 200
        names = {c["name"] for c in response.json()}
        assert names == {"Tenant A Campaign"}


class TestCreateCampaign:
    def test_agent_role_cannot_create(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.post(
            "/campaigns",
            json={"name": "New Campaign"},
            cookies={"voiceos_session": _tenant_token(session_codec, role="AGENT")},
        )
        assert response.status_code == 403

    def test_manager_can_create(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.post(
            "/campaigns",
            json={"name": "New Campaign"},
            cookies={"voiceos_session": _tenant_token(session_codec, role="MANAGER")},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "New Campaign"
        assert body["status"] == "DRAFT"
        assert body["tenant_id"] == TENANT_A


class TestCampaignLifecycle:
    def test_full_happy_path(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        cookies = {"voiceos_session": _tenant_token(session_codec, role="SUPERVISOR")}
        created = app_client.post("/campaigns", json={"name": "Q3"}, cookies=cookies).json()
        campaign_id = created["campaign_id"]

        review = app_client.post(f"/campaigns/{campaign_id}/submit-for-review", cookies=cookies)
        assert review.json()["status"] == "REVIEW"

        approved = app_client.post(f"/campaigns/{campaign_id}/approve", cookies=cookies)
        assert approved.json()["status"] == "APPROVED"

        started = app_client.post(
            f"/campaigns/{campaign_id}/start", json={"target_call_count": 500}, cookies=cookies
        )
        assert started.status_code == 200
        assert started.json()["status"] == "ACTIVE"
        assert started.json()["target_call_count"] == 500

        paused = app_client.post(f"/campaigns/{campaign_id}/pause", cookies=cookies)
        assert paused.json()["status"] == "PAUSED"

        resumed = app_client.post(f"/campaigns/{campaign_id}/resume", cookies=cookies)
        assert resumed.json()["status"] == "ACTIVE"

        completed = app_client.post(f"/campaigns/{campaign_id}/complete", cookies=cookies)
        assert completed.json()["status"] == "COMPLETED"

        archived = app_client.post(f"/campaigns/{campaign_id}/archive", cookies=cookies)
        assert archived.json()["status"] == "ARCHIVED"

    def test_activate_before_approval_returns_409_not_500(
        self, app_client: TestClient, session_codec: WebSessionCodec
    ) -> None:
        cookies = {"voiceos_session": _tenant_token(session_codec, role="SUPERVISOR")}
        created = app_client.post("/campaigns", json={"name": "Skip Steps"}, cookies=cookies).json()
        response = app_client.post(
            f"/campaigns/{created['campaign_id']}/start", json={"target_call_count": 10}, cookies=cookies
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_TRANSITION"

    def test_lifecycle_action_on_unknown_campaign_returns_404(
        self, app_client: TestClient, session_codec: WebSessionCodec
    ) -> None:
        cookies = {"voiceos_session": _tenant_token(session_codec, role="SUPERVISOR")}
        response = app_client.post("/campaigns/does-not-exist/approve", cookies=cookies)
        assert response.status_code == 404


def _campaign(campaign_id: str, tenant_id: str, name: str) -> Campaign:
    from datetime import UTC, datetime

    from src.libs.contracts.models.campaign import AudienceCriteria, RetryPolicy

    now = datetime.now(UTC)
    return Campaign(
        campaign_id=campaign_id,  # type: ignore[arg-type]
        tenant_id=tenant_id,  # type: ignore[arg-type]
        name=name,
        status=CampaignStatus.DRAFT,
        audience_criteria=AudienceCriteria(),
        retry_policy=RetryPolicy(),
        created_at=now,
        updated_at=now,
        created_by="test",
    )
