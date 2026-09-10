"""UserService — user CRUD, invitation workflow, SSO stub façade (V5 Ch8).

Architecture: V5 Ch8 (User & Organization Management).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from src.libs.contracts.models.user import OrgScope, Role, User
from src.libs.contracts.primitives import TenantId
from src.services.authz.roles import ROLE_PERMISSIONS
from src.services.authz.roles import Role as AuthzRole

from .invitation import InvitationService, IssuedInvitation
from .ports import UserRepositoryPort
from .sso_stub import SSOIntegration


class UserService:
    """CRUD over ``users`` plus the invitation/SSO sub-workflows."""

    def __init__(
        self,
        user_repository: UserRepositoryPort,
        invitation_service: InvitationService,
        sso_integration: SSOIntegration | None = None,
    ) -> None:
        self._repo = user_repository
        self._invitations = invitation_service
        self._sso = sso_integration

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get(self, tenant_id: TenantId, user_id: str) -> User | None:
        return self._repo.get_user(tenant_id, user_id)

    def find_by_email(self, tenant_id: TenantId, email: str) -> User | None:
        return self._repo.find_user_by_email(tenant_id, email)

    def list_users(self, tenant_id: TenantId) -> tuple[User, ...]:
        return self._repo.list_users(tenant_id)

    def get_role(self, tenant_id: TenantId, role_id: str) -> Role | None:
        return self._repo.get_role(tenant_id, role_id)

    def list_roles(self, tenant_id: TenantId) -> tuple[Role, ...]:
        return self._repo.list_roles(tenant_id)

    def ensure_system_roles(self, tenant_id: TenantId) -> tuple[Role, ...]:
        """Idempotently ensure all 5 system roles (ADMIN/SUPERVISOR/MANAGER/AGENT/
        AUDITOR) exist as real ``roles`` rows for this tenant, returning all five.

        Generalizes what ``TenantProvisioner._create_default_admin()`` already
        does for ADMIN alone at provisioning time -- every tenant should have
        the full set available to assign via Team Members (ADR-005 Sec 6.8),
        not just whichever role happened to be created first. Permissions are
        seeded from ``authz.roles.ROLE_PERMISSIONS`` -- the DB row remains the
        actual source of truth for enforcement (Law of Authority) once it
        exists; this only controls what it's seeded *with*.
        """
        existing = {role.name: role for role in self._repo.list_roles(tenant_id)}
        now = datetime.now(UTC)
        roles: list[Role] = []
        for authz_role in AuthzRole:
            existing_role = existing.get(authz_role.value)
            if existing_role is not None:
                roles.append(existing_role)
                continue
            new_role = Role(
                role_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                name=authz_role.value,
                description=f"System role: {authz_role.value}",
                permissions=tuple(ROLE_PERMISSIONS[authz_role]),
                is_system_role=True,
                created_at=now,
                updated_at=now,
            )
            self._repo.create_role(new_role)
            roles.append(new_role)
        return tuple(roles)

    def deactivate(self, tenant_id: TenantId, user_id: str) -> User:
        user = self._repo.get_user(tenant_id, user_id)
        if user is None:
            raise ValueError(f"user not found: {user_id}")
        self._repo.set_active_status(tenant_id, user_id, is_active=False)
        return user.model_copy(update={"is_active": False, "updated_at": datetime.now(UTC)})

    # ------------------------------------------------------------------
    # Invitation workflow
    # ------------------------------------------------------------------

    def invite(
        self,
        tenant_id: TenantId,
        email: str,
        role_id: str,
        org_scope: OrgScope,
        invited_by: str,
    ) -> IssuedInvitation:
        return self._invitations.invite(tenant_id, email, role_id, org_scope, invited_by)

    def activate_invitation(self, raw_token: str, name: str) -> User:
        return self._invitations.activate(raw_token, name)

    # ------------------------------------------------------------------
    # SSO (Sprint-025 stub)
    # ------------------------------------------------------------------

    @property
    def sso(self) -> SSOIntegration | None:
        return self._sso
