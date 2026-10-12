"""Integration test: UserRepository role methods against real Postgres (ADR-005 Sec 6.8).

Skipped when POSTGRES_DSN is not set.
Architecture: ADR-005 Sec 6.8 (Team Members).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from src.libs.contracts.models.tenant import Tenant
from src.libs.contracts.models.user import Role
from src.libs.contracts.primitives import TenantId
from src.libs.repositories.invitation import InvitationRepository
from src.libs.repositories.tenant import TenantRepository
from src.libs.repositories.user import UserRepository
from src.services.user_management.invitation import InvitationService
from src.services.user_management.service import UserService
from tests.integration.conftest import requires_postgres

_NOW = datetime(2026, 7, 26, tzinfo=UTC)


@requires_postgres
class TestUserRepositoryRoles:
    def test_create_role_and_list_roles_round_trip(self, pg_conn: Any) -> None:
        tenant_repo = TenantRepository(pg_conn)
        user_repo = UserRepository(pg_conn)

        tenant = Tenant(
            tenant_id=TenantId(str(uuid.uuid4())),
            slug=f"it-roles-{uuid.uuid4()}",
            display_name="IT Roles Tenant",
            subscription_tier="GROWTH",
            created_at=_NOW,
            updated_at=_NOW,
        )
        role_id = str(uuid.uuid4())
        role_ids: list[str] = [role_id]

        try:
            tenant_repo.create(tenant)
            user_repo.create_role(
                Role(
                    role_id=role_id,
                    tenant_id=tenant.tenant_id,
                    name="MANAGER",
                    permissions=("read:all", "write:campaigns"),
                    is_system_role=True,
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )

            fetched_by_id = user_repo.get_role(tenant.tenant_id, role_id)
            assert fetched_by_id is not None
            assert fetched_by_id.name == "MANAGER"
            assert fetched_by_id.permissions == ("read:all", "write:campaigns")

            fetched_by_name = user_repo.get_role_by_name(tenant.tenant_id, "MANAGER")
            assert fetched_by_name is not None
            assert fetched_by_name.role_id == role_id

            all_roles = user_repo.list_roles(tenant.tenant_id)
            assert {r.role_id for r in all_roles} == {role_id}
        finally:
            _delete_roles(pg_conn, role_ids)
            _delete_tenant(pg_conn, tenant.tenant_id)

    def test_ensure_system_roles_against_real_database(self, pg_conn: Any) -> None:
        tenant_repo = TenantRepository(pg_conn)
        user_repo = UserRepository(pg_conn)

        tenant = Tenant(
            tenant_id=TenantId(str(uuid.uuid4())),
            slug=f"it-ensure-roles-{uuid.uuid4()}",
            display_name="IT Ensure Roles Tenant",
            subscription_tier="GROWTH",
            created_at=_NOW,
            updated_at=_NOW,
        )

        try:
            tenant_repo.create(tenant)

            service = UserService(user_repo, InvitationService(InvitationRepository(pg_conn), user_repo))
            first_pass = service.ensure_system_roles(tenant.tenant_id)
            assert {r.name for r in first_pass} == {"ADMIN", "SUPERVISOR", "MANAGER", "AGENT", "AUDITOR"}

            second_pass = service.ensure_system_roles(tenant.tenant_id)
            assert {r.role_id for r in first_pass} == {r.role_id for r in second_pass}

            all_roles = user_repo.list_roles(tenant.tenant_id)
            assert len(all_roles) == 5
        finally:
            _delete_roles(pg_conn, [r.role_id for r in user_repo.list_roles(tenant.tenant_id)])
            _delete_tenant(pg_conn, tenant.tenant_id)


def _delete_roles(conn: Any, role_ids: list[str]) -> None:
    cur = conn.cursor()
    for role_id in role_ids:
        cur.execute("DELETE FROM roles WHERE role_id = %s", (role_id,))
    conn.commit()


def _delete_tenant(conn: Any, tenant_id: str) -> None:
    cur = conn.cursor()
    cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
    conn.commit()
