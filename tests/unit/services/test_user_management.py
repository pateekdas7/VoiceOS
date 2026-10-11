"""Unit tests for User Management (Sprint-021, V5 Ch8).

All tests run fully in-process — no live Postgres required (Phase 1).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from src.libs.contracts.models.user import OrgScope, Role, RoleAssignment, User
from src.libs.contracts.primitives import TenantId
from src.libs.repositories.invitation import Invitation
from src.services.user_management.invitation import (
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationNotPendingError,
    InvitationService,
)
from src.services.user_management.service import UserService
from src.services.user_management.sso_stub import SSOIntegration, SSONotImplementedError

TENANT_ID = TenantId(str(uuid.uuid4()))


class _FakeInvitationRepository:
    def __init__(self) -> None:
        self._by_hash: dict[str, Invitation] = {}

    def create(self, invitation: Invitation) -> Invitation:
        self._by_hash[invitation.token_hash] = invitation
        return invitation

    def get_by_token_hash(self, token_hash: str) -> Invitation | None:
        return self._by_hash.get(token_hash)

    def mark_status(self, invitation_id: str, status: str, *, accepted_at: Any = None) -> int:
        for token_hash, inv in self._by_hash.items():
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
        self.deactivated: list[str] = []
        self.roles: list[Role] = []

    def create_user(self, user: User) -> User:
        self.users.append(user)
        return user

    def assign_role(self, assignment: RoleAssignment) -> RoleAssignment:
        self.assignments.append(assignment)
        return assignment

    def get_user(self, tenant_id: TenantId, user_id: str) -> User | None:
        return next((u for u in self.users if u.user_id == user_id), None)

    def find_user_by_email(self, tenant_id: TenantId, email: str) -> User | None:
        return next((u for u in self.users if u.email == email), None)

    def list_users(self, tenant_id: TenantId) -> tuple[User, ...]:
        return tuple(self.users)

    def set_active_status(self, tenant_id: TenantId, user_id: str, *, is_active: bool) -> int:
        self.deactivated.append(user_id)
        return 1

    def create_role(self, role: Role) -> Role:
        self.roles.append(role)
        return role

    def get_role(self, tenant_id: TenantId, role_id: str) -> Role | None:
        return next((r for r in self.roles if r.role_id == role_id), None)

    def get_role_by_name(self, tenant_id: TenantId, name: str) -> Role | None:
        return next((r for r in self.roles if r.name == name), None)

    def list_roles(self, tenant_id: TenantId) -> tuple[Role, ...]:
        return tuple(self.roles)


def _scope() -> OrgScope:
    return OrgScope(scope_type="BUSINESS_UNIT", scope_id="bu-1")


class TestInvitationService:
    def test_invite_then_activate_creates_user_with_correct_role(self) -> None:
        """Invite sent -> token activation -> user created with correct role (AC)."""
        users = _FakeUserRepository()
        service = InvitationService(_FakeInvitationRepository(), users)

        issued = service.invite(TENANT_ID, "agent@acme.example", "role-agent", _scope(), "admin-1")
        assert issued.invitation.status == "PENDING"

        user = service.activate(issued.raw_token, "New Agent")

        assert user.email == "agent@acme.example"
        assert len(users.assignments) == 1
        assert users.assignments[0].role_id == "role-agent"
        assert users.assignments[0].org_scope.scope_id == "bu-1"

    def test_activate_unknown_token_raises(self) -> None:
        service = InvitationService(_FakeInvitationRepository(), _FakeUserRepository())
        with pytest.raises(InvitationNotFoundError):
            service.activate("no-such-token", "Someone")

    def test_activate_already_accepted_invitation_raises(self) -> None:
        users = _FakeUserRepository()
        service = InvitationService(_FakeInvitationRepository(), users)
        issued = service.invite(TENANT_ID, "agent@acme.example", "role-agent", _scope(), "admin-1")

        service.activate(issued.raw_token, "New Agent")

        with pytest.raises(InvitationNotPendingError):
            service.activate(issued.raw_token, "New Agent Again")

    def test_activate_expired_invitation_raises(self) -> None:
        users = _FakeUserRepository()
        service = InvitationService(_FakeInvitationRepository(), users)
        issued = service.invite(TENANT_ID, "agent@acme.example", "role-agent", _scope(), "admin-1", ttl_hours=-1)

        with pytest.raises(InvitationExpiredError):
            service.activate(issued.raw_token, "New Agent")


class TestUserService:
    def test_deactivate_user(self) -> None:
        users = _FakeUserRepository()
        now = datetime.now(UTC)
        user = User(user_id="u-1", tenant_id=TENANT_ID, email="a@b.com", name="A", created_at=now, updated_at=now)
        users.create_user(user)
        service = UserService(users, InvitationService(_FakeInvitationRepository(), users))

        deactivated = service.deactivate(TENANT_ID, "u-1")

        assert deactivated.is_active is False
        assert users.deactivated == ["u-1"]

    def test_ensure_system_roles_creates_all_five(self) -> None:
        users = _FakeUserRepository()
        service = UserService(users, InvitationService(_FakeInvitationRepository(), users))

        roles = service.ensure_system_roles(TENANT_ID)

        assert {r.name for r in roles} == {"ADMIN", "SUPERVISOR", "MANAGER", "AGENT", "AUDITOR"}
        assert all(r.is_system_role for r in roles)
        assert len(users.roles) == 5

    def test_ensure_system_roles_permissions_match_authz_vocabulary(self) -> None:
        users = _FakeUserRepository()
        service = UserService(users, InvitationService(_FakeInvitationRepository(), users))

        roles = service.ensure_system_roles(TENANT_ID)

        admin_role = next(r for r in roles if r.name == "ADMIN")
        assert set(admin_role.permissions) == {
            "read:all",
            "write:all",
            "write:campaigns",
            "write:users",
            "decide:hitl_items",
        }

    def test_ensure_system_roles_is_idempotent(self) -> None:
        users = _FakeUserRepository()
        service = UserService(users, InvitationService(_FakeInvitationRepository(), users))

        first = service.ensure_system_roles(TENANT_ID)
        second = service.ensure_system_roles(TENANT_ID)

        assert len(users.roles) == 5  # not 10 -- second call reused the existing rows
        assert {r.role_id for r in first} == {r.role_id for r in second}

    def test_ensure_system_roles_preserves_an_already_customized_role(self) -> None:
        """A role that already exists (e.g. seeded at tenant provisioning) is
        reused as-is, never overwritten -- the DB row stays authoritative."""
        users = _FakeUserRepository()
        now = datetime.now(UTC)
        existing_admin = Role(
            role_id="custom-admin-id",
            tenant_id=TENANT_ID,
            name="ADMIN",
            permissions=("read:all", "write:all", "write:campaigns", "write:users"),
            is_system_role=True,
            created_at=now,
            updated_at=now,
        )
        users.roles.append(existing_admin)
        service = UserService(users, InvitationService(_FakeInvitationRepository(), users))

        roles = service.ensure_system_roles(TENANT_ID)

        admin_role = next(r for r in roles if r.name == "ADMIN")
        assert admin_role.role_id == "custom-admin-id"
        assert len(users.roles) == 5

    def test_list_roles_returns_every_role(self) -> None:
        users = _FakeUserRepository()
        service = UserService(users, InvitationService(_FakeInvitationRepository(), users))
        service.ensure_system_roles(TENANT_ID)

        assert len(service.list_roles(TENANT_ID)) == 5

    def test_sso_stub_raises_not_implemented(self) -> None:
        class _FakeConn:
            def cursor(self) -> Any:
                raise AssertionError("authenticate() must never touch the DB — it always raises")

        sso = SSOIntegration(_FakeConn())
        with pytest.raises(SSONotImplementedError):
            sso.authenticate(str(TENANT_ID), "<saml-assertion/>")
