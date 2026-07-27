"""Narrow structural ports ``tenant_management`` depends on.

Same boundary-safe-Protocol precedent as ``PolicyLookupPort``
(``src.engines.dialogue_policy``, Sprint-017) and ``KMSClientProtocol``
(``src.libs.encryption``, Sprint-019): depending on the minimal shape a
collaborator must have (not a concrete SQL-backed class) keeps this service
testable with lightweight in-memory fakes and decoupled from any one
persistence implementation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from src.libs.contracts.models.tenant import Tenant, TenantStatus
from src.libs.contracts.models.user import Role, RoleAssignment, User
from src.libs.contracts.primitives import TenantId


class TenantRepositoryPort(Protocol):
    """The ``tenants`` persistence operations tenant lifecycle logic needs."""

    def create(self, tenant: Tenant) -> Tenant: ...

    def get(self, tenant_id: TenantId) -> Tenant | None: ...

    def get_by_slug(self, slug: str) -> Tenant | None: ...

    def list_all(self) -> tuple[Tenant, ...]: ...

    def update_status(
        self,
        tenant_id: TenantId,
        status: TenantStatus,
        *,
        activated_at: datetime | None = None,
        suspended_at: datetime | None = None,
    ) -> int: ...


class UserProvisioningPort(Protocol):
    """The ``users``/``roles``/``role_assignments`` writes provisioning needs."""

    def create_role(self, role: Role) -> Role: ...

    def create_user(self, user: User) -> User: ...

    def assign_role(self, assignment: RoleAssignment) -> RoleAssignment: ...
