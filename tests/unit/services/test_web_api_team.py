"""Unit tests for Client -> Team Members routes (ADR-005 Sec 6.8).

All tests run fully in-process against a real Starlette TestClient with a
fake UserRepository -- no live Postgres/network required (Phase 1).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from src.libs.contracts.models.user import Role, RoleAssignment, User
from src.libs.contracts.primitives import TenantId
from src.services.authz.roles import ROLE_PERMISSIONS
from src.services.authz.roles import Role as AuthzRole
from src.services.platform_admin.service import PlatformAdminService
from src.services.user_management.invitation import InvitationService
from src.services.user_management.service import UserService
from src.services.web_api.api import create_web_api
from src.services.web_api.session import WebSessionCodec

FRONTEND_URL = "https://app.voiceos.test"
BFF_URL = "https://bff.voiceos.test"
TENANT_A = "tenant-a"


class _FakeUserRepository:
    def __init__(self) -> None:
        self.users: list[User] = []
        self.assignments: list[RoleAssignment] = []
        self.roles: list[Role] = []
        self.deactivated: list[str] = []

    def create_user(self, user: User) -> User:
        self.users.append(user)
        return user

    def assign_role(self, assignment: RoleAssignment) -> RoleAssignment:
        self.assignments.append(assignment)
        return assignment

    def get_user(self, tenant_id: str, user_id: str) -> User | None:
        user = next((u for u in self.users if u.user_id == user_id and u.tenant_id == tenant_id), None)
        if user is None:
            return None
        assignments = tuple(a for a in self.assignments if a.user_id == user_id)
        return user.model_copy(update={"role_assignments": assignments})

    def find_user_by_email(self, tenant_id: str, email: str) -> User | None:
        return next((u for u in self.users if u.email == email and u.tenant_id == tenant_id), None)

    def list_users(self, tenant_id: str) -> tuple[User, ...]:
        result = []
        for u in self.users:
            if u.tenant_id != tenant_id:
                continue
            assignments = tuple(a for a in self.assignments if a.user_id == u.user_id)
            result.append(u.model_copy(update={"role_assignments": assignments}))
        return tuple(result)

    def set_active_status(self, tenant_id: str, user_id: str, *, is_active: bool) -> int:
        for i, u in enumerate(self.users):
            if u.user_id == user_id and u.tenant_id == tenant_id:
                self.users[i] = u.model_copy(update={"is_active": is_active})
                self.deactivated.append(user_id)
                return 1
        return 0

    def create_role(self, role: Role) -> Role:
        self.roles.append(role)
        return role

    def get_role(self, tenant_id: str, role_id: str) -> Role | None:
        return next((r for r in self.roles if r.role_id == role_id and r.tenant_id == tenant_id), None)

    def get_role_by_name(self, tenant_id: str, name: str) -> Role | None:
        return next((r for r in self.roles if r.name == name and r.tenant_id == tenant_id), None)

    def list_roles(self, tenant_id: str) -> tuple[Role, ...]:
        return tuple(r for r in self.roles if r.tenant_id == tenant_id)


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
def user_repo() -> _FakeUserRepository:
    return _FakeUserRepository()


@pytest.fixture
def app_client(session_codec: WebSessionCodec, user_repo: _FakeUserRepository) -> TestClient:
    platform_admin = PlatformAdminService(_NoopPlatformUserRepository())
    invitation_service = InvitationService(_NoopInvitationRepository(), user_repo)
    user_service = UserService(user_repo, invitation_service)

    app = create_web_api(
        session_codec=session_codec,
        google_oauth=_NoopGoogleOAuth(),
        platform_admin=platform_admin,
        user_service=user_service,
        frontend_base_url=FRONTEND_URL,
        bff_public_url=BFF_URL,
        cookie_secure=False,
    )
    return TestClient(app)


def _tenant_token(codec: WebSessionCodec, role: str, tenant_id: str = TENANT_A, subject: str = "actor-1") -> str:
    permissions = tuple(ROLE_PERMISSIONS.get(AuthzRole(role), frozenset()))
    return codec.encode(
        actor_kind="tenant", subject=subject, role=role, permissions=permissions, email="actor@tenant.com",
        tenant_id=tenant_id,
    )


class TestListTeam:
    def test_requires_auth(self, app_client: TestClient) -> None:
        assert app_client.get("/team").status_code == 401

    def test_scoped_to_own_tenant(self, app_client: TestClient, session_codec: WebSessionCodec, user_repo: _FakeUserRepository) -> None:
        now = datetime.now(UTC)
        user_repo.users.append(User(user_id="u-a", tenant_id=TenantId(TENANT_A), email="a@x.com", name="A", created_at=now, updated_at=now))
        user_repo.users.append(User(user_id="u-b", tenant_id=TenantId("tenant-b"), email="b@x.com", name="B", created_at=now, updated_at=now))

        response = app_client.get("/team", cookies={"voiceos_session": _tenant_token(session_codec, "MANAGER")})
        assert response.status_code == 200
        emails = {u["email"] for u in response.json()}
        assert emails == {"a@x.com"}


class TestListRoles:
    def test_lazily_creates_all_five_system_roles(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.get("/team/roles", cookies={"voiceos_session": _tenant_token(session_codec, "MANAGER")})
        assert response.status_code == 200
        names = {r["name"] for r in response.json()}
        assert names == {"ADMIN", "SUPERVISOR", "MANAGER", "AGENT", "AUDITOR"}


class TestInviteTeamMember:
    def test_agent_cannot_invite(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.post(
            "/team/invite",
            json={"email": "new@x.com", "role_id": "does-not-matter"},
            cookies={"voiceos_session": _tenant_token(session_codec, "AGENT")},
        )
        assert response.status_code == 403

    def test_admin_can_invite_with_valid_role(
        self, app_client: TestClient, session_codec: WebSessionCodec, user_repo: _FakeUserRepository
    ) -> None:
        roles_response = app_client.get("/team/roles", cookies={"voiceos_session": _tenant_token(session_codec, "ADMIN")})
        manager_role = next(r for r in roles_response.json() if r["name"] == "MANAGER")

        response = app_client.post(
            "/team/invite",
            json={"email": "new@x.com", "role_id": manager_role["role_id"]},
            cookies={"voiceos_session": _tenant_token(session_codec, "ADMIN")},
        )
        assert response.status_code == 201
        assert response.json()["email"] == "new@x.com"
        assert response.json()["status"] == "PENDING"

    def test_unknown_role_id_returns_422(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.post(
            "/team/invite",
            json={"email": "new@x.com", "role_id": "not-a-real-role-id"},
            cookies={"voiceos_session": _tenant_token(session_codec, "ADMIN")},
        )
        assert response.status_code == 422

    def test_missing_email_returns_422(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.post(
            "/team/invite",
            json={"role_id": "x"},
            cookies={"voiceos_session": _tenant_token(session_codec, "ADMIN")},
        )
        assert response.status_code == 422


class TestDeactivateTeamMember:
    def test_supervisor_can_deactivate(
        self, app_client: TestClient, session_codec: WebSessionCodec, user_repo: _FakeUserRepository
    ) -> None:
        now = datetime.now(UTC)
        user_repo.users.append(
            User(user_id="u-target", tenant_id=TenantId(TENANT_A), email="target@x.com", name="Target", created_at=now, updated_at=now)
        )
        response = app_client.delete(
            "/team/u-target", cookies={"voiceos_session": _tenant_token(session_codec, "SUPERVISOR")}
        )
        assert response.status_code == 200
        assert response.json()["is_active"] is False

    def test_deactivate_unknown_user_returns_404(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.delete(
            "/team/does-not-exist", cookies={"voiceos_session": _tenant_token(session_codec, "SUPERVISOR")}
        )
        assert response.status_code == 404

    def test_agent_cannot_deactivate(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        response = app_client.delete(
            "/team/u-1", cookies={"voiceos_session": _tenant_token(session_codec, "AGENT")}
        )
        assert response.status_code == 403


class TestCrossActorSeparation:
    def test_platform_actor_cannot_access_team_routes(self, app_client: TestClient, session_codec: WebSessionCodec) -> None:
        token = session_codec.encode(
            actor_kind="platform", subject="pu-1", role="PLATFORM_ADMIN", email="a@voiceos.ai", tenant_id=None
        )
        response = app_client.get("/team", cookies={"voiceos_session": token})
        assert response.status_code == 403
