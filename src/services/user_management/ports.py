"""Narrow structural ports ``user_management`` depends on (see tenant_management.ports)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from src.libs.contracts.models.user import RoleAssignment, User
from src.libs.contracts.primitives import TenantId
from src.libs.repositories.invitation import Invitation


class InvitationRepositoryPort(Protocol):
    """The ``invitations`` operations ``InvitationService`` needs."""

    def create(self, invitation: Invitation) -> Invitation: ...

    def get_by_token_hash(self, token_hash: str) -> Invitation | None: ...

    def mark_status(self, invitation_id: str, status: str, *, accepted_at: datetime | None = None) -> int: ...


class UserRepositoryPort(Protocol):
    """The ``users``/``role_assignments`` operations user management needs."""

    def create_user(self, user: User) -> User: ...

    def assign_role(self, assignment: RoleAssignment) -> RoleAssignment: ...

    def get_user(self, tenant_id: TenantId, user_id: str) -> User | None: ...

    def find_user_by_email(self, tenant_id: TenantId, email: str) -> User | None: ...

    def list_users(self, tenant_id: TenantId) -> tuple[User, ...]: ...

    def set_active_status(self, tenant_id: TenantId, user_id: str, *, is_active: bool) -> int: ...
