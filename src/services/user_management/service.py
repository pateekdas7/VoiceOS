"""UserService — user CRUD, invitation workflow, SSO stub façade (V5 Ch8).

Architecture: V5 Ch8 (User & Organization Management).
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.libs.contracts.models.user import OrgScope, User
from src.libs.contracts.primitives import TenantId

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
