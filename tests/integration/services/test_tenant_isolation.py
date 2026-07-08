"""Integration tests: cross-tenant isolation + tenant provisioning (Sprint-021, V5 Ch2/Ch3).

Required named tests: ``test_tenant_isolation_cross_tenant_query``,
``test_tenant_provisioner_creates_kek``.

Runs against real Postgres (migration 0018 applied via the session-scoped
``pg_conn`` fixture); KMS/Redis/EventBus use the Phase 1 mock backends
Sprint-021.md's own table specifies (``FakeKMSClient``/``FakeRedisClient``/
``FakeEventBus`` — no real Vault/Redis/EventBus dependency in Phase 1).

Skipped when POSTGRES_DSN is not set.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from src.libs.contracts.models.tenant import Tenant, TenantStatus
from src.libs.contracts.models.user import OrgScope, Role, RoleAssignment, User
from src.libs.contracts.primitives import TenantId
from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.publisher import Publisher
from src.libs.repositories.tenant import TenantRepository
from src.libs.repositories.user import UserRepository
from src.services.tenant_management.provisioner import TenantProvisioner
from src.services.tenant_management.service import TenantService
from tests.fixtures.fake_kms import FakeKMSClient
from tests.fixtures.redis import FakeRedisClient
from tests.integration.conftest import requires_postgres

_NOW = datetime(2026, 7, 6, tzinfo=UTC)


def _make_tenant(status: TenantStatus = TenantStatus.PRODUCTION) -> Tenant:
    return Tenant(
        tenant_id=TenantId(str(uuid.uuid4())),
        slug=f"tenant-{uuid.uuid4().hex[:12]}",
        display_name="Isolation Test Tenant",
        subscription_tier="GROWTH",
        status=status,
        created_at=_NOW,
        updated_at=_NOW,
    )


@requires_postgres
class TestTenantIsolationCrossTenantQuery:
    def test_tenant_isolation_cross_tenant_query(self, pg_conn: Any) -> None:
        """A user created under tenant-A is invisible to a tenant-B query (required named test)."""
        tenant_repo = TenantRepository(pg_conn)
        user_repo = UserRepository(pg_conn)

        tenant_a = tenant_repo.create(_make_tenant())
        tenant_b = tenant_repo.create(_make_tenant())
        shared_email = f"agent-{uuid.uuid4()}@acme.example"

        try:
            user_repo.create_user(
                User(
                    user_id=str(uuid.uuid4()),
                    tenant_id=tenant_a.tenant_id,
                    email=shared_email,
                    name="Tenant-A Agent",
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )

            # Visible to its own tenant.
            assert user_repo.find_user_by_email(tenant_a.tenant_id, shared_email) is not None

            # Invisible under a different tenant — same email, different tenant_id.
            assert user_repo.find_user_by_email(tenant_b.tenant_id, shared_email) is None
            assert user_repo.list_users(tenant_b.tenant_id) == ()
        finally:
            cur = pg_conn.cursor()
            cur.execute("DELETE FROM users WHERE email = %s", (shared_email,))
            cur.execute("DELETE FROM tenants WHERE tenant_id IN (%s, %s)", (tenant_a.tenant_id, tenant_b.tenant_id))
            pg_conn.commit()

    def test_role_assignment_scoped_to_owning_user_only(self, pg_conn: Any) -> None:
        tenant_repo = TenantRepository(pg_conn)
        user_repo = UserRepository(pg_conn)
        tenant = tenant_repo.create(_make_tenant())

        role = user_repo.create_role(
            Role(
                role_id=str(uuid.uuid4()),
                tenant_id=tenant.tenant_id,
                name="AGENT",
                permissions=("read:all",),
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        user = user_repo.create_user(
            User(
                user_id=str(uuid.uuid4()),
                tenant_id=tenant.tenant_id,
                email=f"agent-{uuid.uuid4()}@acme.example",
                name="Agent",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )

        try:
            user_repo.assign_role(
                RoleAssignment(
                    assignment_id=str(uuid.uuid4()),
                    user_id=user.user_id,
                    role_id=role.role_id,
                    org_scope=OrgScope(scope_type="TENANT", scope_id=str(tenant.tenant_id)),
                    assigned_by=str(uuid.uuid4()),
                    assigned_at=_NOW,
                )
            )

            assignments = user_repo.get_role_assignments(tenant.tenant_id, user.user_id)
            assert len(assignments) == 1
            assert assignments[0].role_id == role.role_id
        finally:
            cur = pg_conn.cursor()
            cur.execute("DELETE FROM role_assignments WHERE user_id = %s", (user.user_id,))
            cur.execute("DELETE FROM users WHERE user_id = %s", (user.user_id,))
            cur.execute("DELETE FROM roles WHERE role_id = %s", (role.role_id,))
            cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant.tenant_id,))
            pg_conn.commit()


@requires_postgres
class TestTenantProvisionerCreatesKek:
    def test_tenant_provisioner_creates_kek(self, pg_conn: Any) -> None:
        """Activation -> KMS key created (required named test).

        Exercises the full ``TenantService.activate_production()`` path
        against a real Postgres ``tenants`` row; KMS/Redis/EventBus use
        Sprint-021.md's own specified Phase 1 mocks.
        """
        tenant_repo = TenantRepository(pg_conn)
        tenant = tenant_repo.create(_make_tenant(status=TenantStatus.TRIAL))

        kms = FakeKMSClient()
        redis = FakeRedisClient()
        publisher = Publisher(EventBus(redis))
        provisioner = TenantProvisioner(
            user_repository=_NullUserRepository(), kms_client=kms, redis=redis, publisher=publisher
        )
        service = TenantService(tenant_repo, provisioner=provisioner)

        try:
            service.move_to_sandbox(tenant.tenant_id)
            activated, result = service.activate_production(tenant.tenant_id, "admin@acme.example", "Admin")

            assert activated.status is TenantStatus.PRODUCTION
            assert result.kek_id == f"tenant-{tenant.tenant_id}"
            assert result.kek_id in kms._keks
        finally:
            cur = pg_conn.cursor()
            cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant.tenant_id,))
            pg_conn.commit()


class _NullUserRepository:
    """A no-op ``UserProvisioningPort`` — this test only asserts on the KEK, not the admin user."""

    def create_role(self, role: Any) -> Any:
        return role

    def create_user(self, user: Any) -> Any:
        return user

    def assign_role(self, assignment: Any) -> Any:
        return assignment


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
