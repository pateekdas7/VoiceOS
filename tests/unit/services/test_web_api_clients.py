"""Unit tests for Admin -> Clients CRUD routes (ADR-005 Sec 6.1).

All tests run fully in-process against a real Starlette TestClient with a
fake TenantRepository -- no live Postgres/network required (Phase 1).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from src.libs.contracts.models.tenant import Tenant, TenantStatus
from src.libs.contracts.primitives import TenantId
from src.services.platform_admin.service import PlatformAdminService
from src.services.tenant_management.service import TenantService
from src.services.user_management.invitation import InvitationService
from src.services.user_management.service import UserService
from src.services.web_api.api import create_web_api
from src.services.web_api.session import WebSessionCodec

FRONTEND_URL = "https://app.voiceos.test"
BFF_URL = "https://bff.voiceos.test"


class _FakeTenantRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, Tenant] = {}

    def create(self, tenant: Tenant) -> Tenant:
        self._by_id[tenant.tenant_id] = tenant
        return tenant

    def get(self, tenant_id: str) -> Tenant | None:
        return self._by_id.get(tenant_id)

    def get_by_slug(self, slug: str) -> Tenant | None:
        return next((t for t in self._by_id.values() if t.slug == slug), None)

    def list_all(self) -> tuple[Tenant, ...]:
        return tuple(sorted(self._by_id.values(), key=lambda t: t.created_at))

    def update_status(
        self, tenant_id: str, status: TenantStatus, *, activated_at: Any = None, suspended_at: Any = None
    ) -> int:
        tenant = self._by_id.get(tenant_id)
        if tenant is None:
            return 0
        self._by_id[tenant_id] = tenant.model_copy(update={"status": status, "updated_at": datetime.now(UTC)})
        return 1


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
def tenant_repo() -> _FakeTenantRepository:
    return _FakeTenantRepository()


@pytest.fixture
def app_client(session_codec: WebSessionCodec, tenant_repo: _FakeTenantRepository) -> TestClient:
    platform_admin = PlatformAdminService(_NoopPlatformUserRepository())
    invitation_service = InvitationService(_NoopInvitationRepository(), _NoopUserRepository())
    user_service = UserService(_NoopUserRepository(), invitation_service)
    tenant_service = TenantService(tenant_repo)

    app = create_web_api(
        session_codec=session_codec,
        google_oauth=_NoopGoogleOAuth(),
        platform_admin=platform_admin,
        user_service=user_service,
        tenant_service=tenant_service,
        frontend_base_url=FRONTEND_URL,
        bff_public_url=BFF_URL,
        cookie_secure=False,
    )
    return TestClient(app)


def _admin_token(codec: WebSessionCodec, role: str = "PLATFORM_ADMIN") -> str:
    return codec.encode(actor_kind="platform", subject="pu-1", role=role, email="admin@voiceos.ai", tenant_id=None)


def _tenant_token(codec: WebSessionCodec) -> str:
    return codec.encode(actor_kind="tenant", subject="u-1", role="ADMIN", email="u@tenant.com", tenant_id="t-1")


class TestListClients:
    def test_requires_auth(self, app_client: TestClient) -> None:
        response = app_client.get("/admin/clients")
        assert response.status_code == 401

    def test_rejects_tenant_actor(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.get("/admin/clients", cookies={"voiceos_session": _tenant_token(session_codec)})
        assert response.status_code == 403

    def test_platform_admin_can_list(
        self, app_client: TestClient, session_codec: WebSessionCodec, tenant_repo: _FakeTenantRepository
    ) -> None:
        tenant_repo.create(
            Tenant(
                tenant_id=TenantId("t-1"),
                slug="acme",
                display_name="Acme",
                subscription_tier="GROWTH",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        response = app_client.get("/admin/clients", cookies={"voiceos_session": _admin_token(session_codec)})
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["slug"] == "acme"

    def test_platform_support_can_list_read_only(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.get(
            "/admin/clients", cookies={"voiceos_session": _admin_token(session_codec, role="PLATFORM_SUPPORT")}
        )
        assert response.status_code == 200


class TestCreateClient:
    def test_requires_write_permission(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.post(
            "/admin/clients",
            json={"slug": "acme", "display_name": "Acme", "subscription_tier": "GROWTH"},
            cookies={"voiceos_session": _admin_token(session_codec, role="PLATFORM_SUPPORT")},
        )
        assert response.status_code == 403

    def test_platform_admin_can_create(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.post(
            "/admin/clients",
            json={"slug": "acme-collections", "display_name": "Acme Collections", "subscription_tier": "GROWTH"},
            cookies={"voiceos_session": _admin_token(session_codec)},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["slug"] == "acme-collections"
        assert body["status"] == "TRIAL"

    def test_missing_required_field_returns_422(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.post(
            "/admin/clients",
            json={"display_name": "Acme"},
            cookies={"voiceos_session": _admin_token(session_codec)},
        )
        assert response.status_code == 422


class TestGetClient:
    def test_not_found_returns_404(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.get(
            "/admin/clients/does-not-exist", cookies={"voiceos_session": _admin_token(session_codec)}
        )
        assert response.status_code == 404

    def test_found_returns_tenant(
        self, app_client: TestClient, session_codec: WebSessionCodec, tenant_repo: _FakeTenantRepository
    ) -> None:
        tenant_repo.create(
            Tenant(
                tenant_id=TenantId("t-42"),
                slug="beta",
                display_name="Beta",
                subscription_tier="STARTER",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        response = app_client.get("/admin/clients/t-42", cookies={"voiceos_session": _admin_token(session_codec)})
        assert response.status_code == 200
        assert response.json()["slug"] == "beta"


class TestSuspendClient:
    def test_suspend_transitions_status(
        self, app_client: TestClient, session_codec: WebSessionCodec, tenant_repo: _FakeTenantRepository
    ) -> None:
        tenant_repo.create(
            Tenant(
                tenant_id=TenantId("t-99"),
                slug="gamma",
                display_name="Gamma",
                subscription_tier="GROWTH",
                status=TenantStatus.PRODUCTION,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        response = app_client.post(
            "/admin/clients/t-99/suspend", cookies={"voiceos_session": _admin_token(session_codec)}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "SUSPENDED"

    def test_suspend_unknown_client_returns_404(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.post(
            "/admin/clients/nope/suspend", cookies={"voiceos_session": _admin_token(session_codec)}
        )
        assert response.status_code == 404

    def test_suspend_invalid_transition_returns_409_not_500(
        self, app_client: TestClient, session_codec: WebSessionCodec, tenant_repo: _FakeTenantRepository
    ) -> None:
        """Regression test: a TRIAL tenant has no valid TRIAL->SUSPENDED transition.

        Found via live end-to-end testing against real Postgres, not by this
        unit suite (the original tests only ever seeded PRODUCTION tenants) --
        InvalidTenantTransitionError was escaping as an unhandled 500.
        """
        tenant_repo.create(
            Tenant(
                tenant_id=TenantId("t-trial"),
                slug="delta",
                display_name="Delta",
                subscription_tier="GROWTH",
                status=TenantStatus.TRIAL,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        response = app_client.post(
            "/admin/clients/t-trial/suspend", cookies={"voiceos_session": _admin_token(session_codec)}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_TRANSITION"

    def test_read_only_role_cannot_suspend(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.post(
            "/admin/clients/t-1/suspend",
            cookies={"voiceos_session": _admin_token(session_codec, role="PLATFORM_SUPPORT")},
        )
        assert response.status_code == 403
