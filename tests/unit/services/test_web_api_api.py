"""Unit tests for the Web BFF Starlette app (ADR-005 Sec 4.1).

All tests run fully in-process against a real Starlette TestClient with
fake ports -- no live Postgres/network required (Phase 1).
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from src.libs.contracts.models.platform_user import PlatformUser
from src.libs.contracts.models.user import Role, RoleAssignment, User
from src.libs.health.aggregator import HealthAggregator
from src.libs.health.protocol import HealthStatus
from src.libs.repositories.invitation import Invitation
from src.services.platform_admin.service import PlatformAdminService
from src.services.user_management.invitation import InvitationService
from src.services.user_management.service import UserService
from src.services.web_api.api import create_web_api
from src.services.web_api.google_oauth import GoogleIdentity, GoogleIdentityError
from src.services.web_api.session import WebSessionCodec

FRONTEND_URL = "https://app.voiceos.test"
BFF_URL = "https://bff.voiceos.test"


class _FakePlatformUserRepository:
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


class _FakeInvitationRepository:
    def __init__(self) -> None:
        self._by_hash: dict[str, Invitation] = {}

    def create(self, invitation: Invitation) -> Invitation:
        self._by_hash[invitation.token_hash] = invitation
        return invitation

    def get_by_token_hash(self, token_hash: str) -> Invitation | None:
        return self._by_hash.get(token_hash)

    def mark_status(self, invitation_id: str, status: str, *, accepted_at: datetime | None = None) -> int:
        for token_hash, inv in list(self._by_hash.items()):
            if inv.invitation_id == invitation_id:
                self._by_hash[token_hash] = Invitation(
                    invitation_id=inv.invitation_id,
                    tenant_id=inv.tenant_id,
                    email=inv.email,
                    role_id=inv.role_id,
                    org_scope_type=inv.org_scope_type,
                    org_scope_id=inv.org_scope_id,
                    token_hash=inv.token_hash,
                    status=status,
                    invited_by=inv.invited_by,
                    expires_at=inv.expires_at,
                    created_at=inv.created_at,
                    accepted_at=accepted_at,
                )
                return 1
        return 0


class _FakeUserRepository:
    def __init__(self) -> None:
        self.users: list[User] = []
        self.assignments: list[RoleAssignment] = []
        self.roles: list[Role] = []

    def create_user(self, user: User) -> User:
        self.users.append(user)
        return user

    def assign_role(self, assignment: RoleAssignment) -> RoleAssignment:
        self.assignments.append(assignment)
        return assignment

    def get_user(self, tenant_id: object, user_id: str) -> User | None:
        user = next((u for u in self.users if u.user_id == user_id), None)
        if user is None:
            return None
        assignments = tuple(a for a in self.assignments if a.user_id == user_id)
        return user.model_copy(update={"role_assignments": assignments})

    def find_user_by_email(self, tenant_id: object, email: str) -> User | None:
        return next((u for u in self.users if u.email == email), None)

    def list_users(self, tenant_id: object) -> tuple[User, ...]:
        return tuple(self.users)

    def set_active_status(self, tenant_id: object, user_id: str, *, is_active: bool) -> int:
        return 0

    def create_role(self, role: Role) -> Role:
        self.roles.append(role)
        return role

    def get_role(self, tenant_id: object, role_id: str) -> Role | None:
        return next((r for r in self.roles if r.role_id == role_id), None)

    def get_role_by_name(self, tenant_id: object, name: str) -> Role | None:
        return next((r for r in self.roles if r.name == name), None)


class _FakeGoogleOAuth:
    def __init__(self) -> None:
        self.identity_by_code: dict[str, GoogleIdentity] = {}

    def build_authorize_url(self, *, state: str, redirect_uri: str) -> str:
        return f"https://accounts.google.com/fake-authorize?state={state}&redirect_uri={redirect_uri}"

    async def resolve_verified_email(self, *, code: str, redirect_uri: str) -> GoogleIdentity:
        identity = self.identity_by_code.get(code)
        if identity is None:
            raise GoogleIdentityError("no fake identity registered for this code")
        return identity


@pytest.fixture
def keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def session_codec(keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]) -> WebSessionCodec:
    private_key, public_key = keypair
    return WebSessionCodec(private_key, public_key)


@pytest.fixture
def platform_repo() -> _FakePlatformUserRepository:
    return _FakePlatformUserRepository()


@pytest.fixture
def user_repo() -> _FakeUserRepository:
    return _FakeUserRepository()


@pytest.fixture
def invitation_repo() -> _FakeInvitationRepository:
    return _FakeInvitationRepository()


@pytest.fixture
def google_oauth() -> _FakeGoogleOAuth:
    return _FakeGoogleOAuth()


@pytest.fixture
def app_client(
    session_codec: WebSessionCodec,
    platform_repo: _FakePlatformUserRepository,
    user_repo: _FakeUserRepository,
    invitation_repo: _FakeInvitationRepository,
    google_oauth: _FakeGoogleOAuth,
) -> TestClient:
    platform_admin = PlatformAdminService(platform_repo)
    invitation_service = InvitationService(invitation_repo, user_repo)
    user_service = UserService(user_repo, invitation_service)
    aggregator = HealthAggregator()

    app = create_web_api(
        session_codec=session_codec,
        google_oauth=google_oauth,  # type: ignore[arg-type]
        platform_admin=platform_admin,
        user_service=user_service,
        frontend_base_url=FRONTEND_URL,
        bff_public_url=BFF_URL,
        health_aggregator=aggregator,
        cookie_secure=False,  # TestClient doesn't use HTTPS
    )
    return TestClient(app, follow_redirects=False)


def _decode_state_from_redirect(location: str) -> str:
    from urllib.parse import parse_qs, urlparse

    return parse_qs(urlparse(location).query)["state"][0]


class TestGoogleStart:
    def test_redirects_to_google_authorize_url(self, app_client: TestClient) -> None:
        response = app_client.get("/auth/google/start")
        assert response.status_code == 302
        assert response.headers["location"].startswith("https://accounts.google.com/fake-authorize")

    def test_encodes_next_and_invitation_token_in_state(self, app_client: TestClient) -> None:
        response = app_client.get("/auth/google/start?next=/client/campaigns&invitation_token=tok-123")
        state = _decode_state_from_redirect(response.headers["location"])
        payload = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
        assert payload["next"] == "/client/campaigns"
        assert payload["invitation_token"] == "tok-123"


class TestGoogleCallbackPlatformActor:
    def test_platform_user_gets_session_cookie_and_redirect(
        self, app_client: TestClient, platform_repo: _FakePlatformUserRepository, google_oauth: _FakeGoogleOAuth
    ) -> None:
        now = datetime.now(UTC)
        platform_repo.users.append(
            PlatformUser(
                platform_user_id="pu-1",
                email="admin@voiceos.ai",
                name="Admin",
                platform_role="PLATFORM_ADMIN",
                created_at=now,
                updated_at=now,
            )
        )
        google_oauth.identity_by_code["good-code"] = GoogleIdentity(
            email="admin@voiceos.ai", name="Admin", email_verified=True
        )

        start = app_client.get("/auth/google/start")
        state = _decode_state_from_redirect(start.headers["location"])

        response = app_client.get(f"/auth/google/callback?code=good-code&state={state}")

        assert response.status_code == 302
        assert response.headers["location"] == f"{FRONTEND_URL}/admin/dashboard"
        assert response.cookies.get("voiceos_actor_kind") == "platform"
        assert response.cookies.get("voiceos_session") is not None

    def test_platform_session_cookie_decodes_correctly(
        self,
        app_client: TestClient,
        platform_repo: _FakePlatformUserRepository,
        google_oauth: _FakeGoogleOAuth,
        session_codec: WebSessionCodec,
    ) -> None:
        now = datetime.now(UTC)
        platform_repo.users.append(
            PlatformUser(
                platform_user_id="pu-2",
                email="support@voiceos.ai",
                name="Support",
                platform_role="PLATFORM_SUPPORT",
                created_at=now,
                updated_at=now,
            )
        )
        google_oauth.identity_by_code["code-2"] = GoogleIdentity(
            email="support@voiceos.ai", name="Support", email_verified=True
        )
        start = app_client.get("/auth/google/start")
        state = _decode_state_from_redirect(start.headers["location"])
        response = app_client.get(f"/auth/google/callback?code=code-2&state={state}")

        token = response.cookies.get("voiceos_session")
        session = session_codec.decode(token)
        assert session is not None
        assert session.actor_kind == "platform"
        assert session.tenant_id is None
        assert session.role == "PLATFORM_SUPPORT"

    def test_respects_next_param_through_the_round_trip(
        self, app_client: TestClient, platform_repo: _FakePlatformUserRepository, google_oauth: _FakeGoogleOAuth
    ) -> None:
        now = datetime.now(UTC)
        platform_repo.users.append(
            PlatformUser(
                platform_user_id="pu-3",
                email="a@voiceos.ai",
                name="A",
                platform_role="PLATFORM_ADMIN",
                created_at=now,
                updated_at=now,
            )
        )
        google_oauth.identity_by_code["code-3"] = GoogleIdentity(email="a@voiceos.ai", name="A", email_verified=True)
        start = app_client.get("/auth/google/start?next=/admin/clients")
        state = _decode_state_from_redirect(start.headers["location"])
        response = app_client.get(f"/auth/google/callback?code=code-3&state={state}")
        assert response.headers["location"] == f"{FRONTEND_URL}/admin/clients"


class TestGoogleCallbackTenantInvitation:
    def test_accepting_invitation_gets_tenant_session_with_role(
        self,
        app_client: TestClient,
        invitation_repo: _FakeInvitationRepository,
        user_repo: _FakeUserRepository,
        google_oauth: _FakeGoogleOAuth,
        session_codec: WebSessionCodec,
    ) -> None:
        now = datetime.now(UTC)
        raw_token = "invite-raw-token"
        import hashlib

        # role_assignments.role_id (and invitations.role_id) are real UUID FKs
        # to the roles table -- seed a real Role row, not a bare role name.
        role_id = str(uuid.uuid4())
        user_repo.roles.append(
            Role(
                role_id=role_id,
                tenant_id="tenant-1",
                name="MANAGER",
                permissions=("read:all", "write:campaigns", "view:analytics"),
                created_at=now,
                updated_at=now,
            )
        )

        invitation_repo._by_hash[hashlib.sha256(raw_token.encode()).hexdigest()] = Invitation(
            invitation_id=str(uuid.uuid4()),
            tenant_id="tenant-1",
            email="newuser@tenant.com",
            role_id=role_id,
            org_scope_type="TENANT",
            org_scope_id="tenant-1",
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            status="PENDING",
            invited_by="admin-user",
            expires_at=now.replace(year=now.year + 1),
            created_at=now,
        )
        google_oauth.identity_by_code["invite-code"] = GoogleIdentity(
            email="newuser@tenant.com", name="New User", email_verified=True
        )

        start = app_client.get(f"/auth/google/start?invitation_token={raw_token}")
        state = _decode_state_from_redirect(start.headers["location"])
        response = app_client.get(f"/auth/google/callback?code=invite-code&state={state}")

        assert response.status_code == 302
        assert response.headers["location"] == f"{FRONTEND_URL}/client/dashboard"
        assert response.cookies.get("voiceos_actor_kind") == "tenant"

        token = response.cookies.get("voiceos_session")
        session = session_codec.decode(token)
        assert session is not None
        assert session.actor_kind == "tenant"
        assert session.tenant_id == "tenant-1"
        assert session.role == "MANAGER"
        assert set(session.permissions) == {"read:all", "write:campaigns", "view:analytics"}
        assert session.email == "newuser@tenant.com"

    def test_invalid_invitation_token_redirects_to_signup_error(
        self, app_client: TestClient, google_oauth: _FakeGoogleOAuth
    ) -> None:
        google_oauth.identity_by_code["c"] = GoogleIdentity(email="x@y.com", name="X", email_verified=True)
        start = app_client.get("/auth/google/start?invitation_token=not-a-real-token")
        state = _decode_state_from_redirect(start.headers["location"])
        response = app_client.get(f"/auth/google/callback?code=c&state={state}")
        assert response.headers["location"] == f"{FRONTEND_URL}/signup?error=invalid_invitation"


class TestGoogleCallbackNoAccount:
    def test_unregistered_email_with_no_invitation_redirects_with_error(
        self, app_client: TestClient, google_oauth: _FakeGoogleOAuth
    ) -> None:
        google_oauth.identity_by_code["c"] = GoogleIdentity(
            email="nobody@nowhere.com", name="Nobody", email_verified=True
        )
        start = app_client.get("/auth/google/start")
        state = _decode_state_from_redirect(start.headers["location"])
        response = app_client.get(f"/auth/google/callback?code=c&state={state}")
        assert response.headers["location"] == f"{FRONTEND_URL}/login?error=no_account"

    def test_google_auth_failure_redirects_with_error(self, app_client: TestClient) -> None:
        response = app_client.get("/auth/google/callback?code=unregistered-code&state=")
        assert response.headers["location"] == f"{FRONTEND_URL}/login?error=google_auth_failed"

    def test_missing_code_redirects_with_error(self, app_client: TestClient) -> None:
        response = app_client.get("/auth/google/callback?state=")
        assert response.headers["location"] == f"{FRONTEND_URL}/login?error=missing_code"


class TestLogout:
    def test_clears_cookies_and_redirects_to_login(self, app_client: TestClient) -> None:
        response = app_client.get("/auth/logout")
        assert response.status_code == 302
        assert response.headers["location"] == f"{FRONTEND_URL}/login"
        set_cookie_headers = response.headers.get_list("set-cookie")
        assert any("voiceos_session=" in h and ("Max-Age=0" in h or "expires=" in h.lower()) for h in set_cookie_headers)


class TestCORS:
    """The frontend is a different origin from this BFF (dev: localhost:3000 vs.
    localhost:8100; prod: app.voiceos.ai vs. api.voiceos.ai) -- every fetch() in
    frontend/lib/api/*.ts sends credentials: "include", which a real browser
    silently blocks without these headers. curl-based verification never
    catches a missing/misconfigured CORSMiddleware, since curl doesn't enforce
    CORS -- this test is the one that actually would."""

    def test_preflight_allows_frontend_origin_with_credentials(self, app_client: TestClient) -> None:
        response = app_client.options(
            "/hitl/queue",
            headers={
                "Origin": FRONTEND_URL,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == FRONTEND_URL
        assert response.headers["access-control-allow-credentials"] == "true"

    def test_actual_response_carries_cors_headers(self, app_client: TestClient) -> None:
        response = app_client.get("/system/health", headers={"Origin": FRONTEND_URL})
        assert response.headers["access-control-allow-origin"] == FRONTEND_URL
        assert response.headers["access-control-allow-credentials"] == "true"

    def test_unrecognized_origin_is_not_echoed_back(self, app_client: TestClient) -> None:
        response = app_client.get("/system/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in response.headers


class TestSystemHealth:
    def test_returns_empty_list_when_no_aggregator(
        self,
        session_codec: WebSessionCodec,
        platform_repo: _FakePlatformUserRepository,
        user_repo: _FakeUserRepository,
        invitation_repo: _FakeInvitationRepository,
        google_oauth: _FakeGoogleOAuth,
    ) -> None:
        platform_admin = PlatformAdminService(platform_repo)
        invitation_service = InvitationService(invitation_repo, user_repo)
        user_service = UserService(user_repo, invitation_service)
        app = create_web_api(
            session_codec=session_codec,
            google_oauth=google_oauth,  # type: ignore[arg-type]
            platform_admin=platform_admin,
            user_service=user_service,
            frontend_base_url=FRONTEND_URL,
            bff_public_url=BFF_URL,
            health_aggregator=None,
            cookie_secure=False,
        )
        client = TestClient(app)
        response = client.get("/system/health")
        assert response.status_code == 200
        assert response.json() == []

    async def test_returns_component_statuses_when_aggregator_present(self, app_client: TestClient) -> None:
        response = app_client.get("/system/health")
        assert response.status_code == 200
        assert response.json() == []  # no checks registered in the fixture's empty aggregator


class _FakeHealthCheck:
    def __init__(self, name: str, status: HealthStatus) -> None:
        self.name = name
        self._status = status

    async def check(self) -> HealthStatus:
        return self._status


class TestSystemHealthWithRegisteredChecks:
    def test_reports_each_registered_component(
        self,
        session_codec: WebSessionCodec,
        platform_repo: _FakePlatformUserRepository,
        user_repo: _FakeUserRepository,
        invitation_repo: _FakeInvitationRepository,
        google_oauth: _FakeGoogleOAuth,
    ) -> None:
        platform_admin = PlatformAdminService(platform_repo)
        invitation_service = InvitationService(invitation_repo, user_repo)
        user_service = UserService(user_repo, invitation_service)
        aggregator = HealthAggregator([_FakeHealthCheck("redis", HealthStatus.HEALTHY)])
        app = create_web_api(
            session_codec=session_codec,
            google_oauth=google_oauth,  # type: ignore[arg-type]
            platform_admin=platform_admin,
            user_service=user_service,
            frontend_base_url=FRONTEND_URL,
            bff_public_url=BFF_URL,
            health_aggregator=aggregator,
            cookie_secure=False,
        )
        client = TestClient(app)
        response = client.get("/system/health")
        assert response.json() == [{"component": "redis", "status": "healthy", "latencyMs": None, "lastChecked": None}]
