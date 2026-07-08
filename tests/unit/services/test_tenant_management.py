"""Unit tests for Tenant Management (Sprint-021, V5 Ch2/Ch3).

All tests run fully in-process — no live Postgres/Redis required (Phase 1).
Repository interactions use small in-memory fake doubles (mirrors the
``FakeRedisClient``/``fake_pg`` precedent); KMS/Redis/EventBus use the
existing project-wide fakes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from src.libs.audit.logger import AuditLogger
from src.libs.contracts.models.tenant import IsolationProfile, Tenant, TenantStatus
from src.libs.contracts.models.user import Role, RoleAssignment, User
from src.libs.contracts.primitives import TenantId
from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.publisher import Publisher
from src.services.tenant_management.deletion import TenantDeleter
from src.services.tenant_management.isolation import (
    IsolationProfileManager,
    UnsafeTenantIdentifierError,
)
from src.services.tenant_management.lifecycle import InvalidTenantTransitionError, TenantLifecycle
from src.services.tenant_management.provisioner import TENANT_PROVISIONED_EVENT_TYPE, TenantProvisioner
from src.services.tenant_management.service import TenantService
from src.services.tenant_management.suspension import TenantSuspender
from tests.fixtures.fake_kms import FakeKMSClient
from tests.fixtures.fake_pg import FakeConnection
from tests.fixtures.redis import FakeRedisClient


class _StubAuditRepository:
    """Minimal in-memory ``AuditRepository`` double — records every append() call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def append(
        self,
        tenant_id: Any,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
        *,
        event_payload: dict[str, Any] | None = None,
        ip_address: str = "",
    ) -> str:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "outcome": outcome,
                "event_payload": event_payload,
            }
        )
        return f"hash-{len(self.calls)}"


class _FakeTenantRepository:
    """In-memory ``TenantRepository`` double keyed by tenant_id."""

    def __init__(self) -> None:
        self._by_id: dict[str, Tenant] = {}

    def create(self, tenant: Tenant) -> Tenant:
        self._by_id[tenant.tenant_id] = tenant
        return tenant

    def get(self, tenant_id: str) -> Tenant | None:
        return self._by_id.get(tenant_id)

    def get_by_slug(self, slug: str) -> Tenant | None:
        return next((t for t in self._by_id.values() if t.slug == slug), None)

    def update_status(
        self,
        tenant_id: str,
        status: TenantStatus,
        *,
        activated_at: datetime | None = None,
        suspended_at: datetime | None = None,
    ) -> int:
        tenant = self._by_id.get(tenant_id)
        if tenant is None:
            return 0
        self._by_id[tenant_id] = tenant.model_copy(update={"status": status, "updated_at": datetime.now(UTC)})
        return 1


class _FakeUserRepository:
    """In-memory ``UserRepository`` double."""

    def __init__(self) -> None:
        self.roles: list[Role] = []
        self.users: list[User] = []
        self.assignments: list[RoleAssignment] = []

    def create_role(self, role: Role) -> Role:
        self.roles.append(role)
        return role

    def create_user(self, user: User) -> User:
        self.users.append(user)
        return user

    def assign_role(self, assignment: RoleAssignment) -> RoleAssignment:
        self.assignments.append(assignment)
        return assignment


def _make_tenant(status: TenantStatus = TenantStatus.TRIAL) -> Tenant:
    now = datetime.now(UTC)
    return Tenant(
        tenant_id=TenantId(str(uuid.uuid4())),
        slug="acme-collections",
        display_name="Acme Collections",
        subscription_tier="GROWTH",
        status=status,
        created_at=now,
        updated_at=now,
    )


class TestTenantLifecycle:
    def test_tenant_lifecycle_valid_transitions(self) -> None:
        """TRIAL -> SANDBOX -> PRODUCTION each passes (required named test)."""
        status = TenantLifecycle.transition(TenantStatus.TRIAL, TenantStatus.SANDBOX)
        assert status is TenantStatus.SANDBOX
        status = TenantLifecycle.transition(status, TenantStatus.PRODUCTION)
        assert status is TenantStatus.PRODUCTION

    def test_tenant_lifecycle_invalid_transition(self) -> None:
        """DELETED -> PRODUCTION raises (required named test)."""
        with pytest.raises(InvalidTenantTransitionError):
            TenantLifecycle.transition(TenantStatus.DELETED, TenantStatus.PRODUCTION)

    def test_suspend_then_reactivate_is_valid(self) -> None:
        status = TenantLifecycle.transition(TenantStatus.PRODUCTION, TenantStatus.SUSPENDED)
        assert status is TenantStatus.SUSPENDED
        status = TenantLifecycle.transition(status, TenantStatus.PRODUCTION)
        assert status is TenantStatus.PRODUCTION

    def test_full_forward_chain_to_deleted(self) -> None:
        status = TenantStatus.SUSPENDED
        for target in (TenantStatus.CANCELLED, TenantStatus.DELETING, TenantStatus.DELETED):
            status = TenantLifecycle.transition(status, target)
        assert status is TenantStatus.DELETED

    def test_same_state_transition_is_invalid(self) -> None:
        with pytest.raises(InvalidTenantTransitionError):
            TenantLifecycle.transition(TenantStatus.TRIAL, TenantStatus.TRIAL)


class TestIsolationProfileManager:
    def test_shared_profile_is_a_noop(self) -> None:
        manager = IsolationProfileManager()
        conn = FakeConnection()
        name = manager.provision(str(uuid.uuid4()), IsolationProfile.SHARED, conn)
        assert name == ""
        assert conn.cursor_obj.executed == []

    def test_dedicated_schema_creates_schema(self) -> None:
        manager = IsolationProfileManager()
        conn = FakeConnection()
        tenant_id = str(uuid.uuid4())
        name = manager.provision(tenant_id, IsolationProfile.DEDICATED_SCHEMA, conn)
        assert name.startswith("tenant_")
        assert any("CREATE SCHEMA" in sql for sql, _ in conn.cursor_obj.executed)
        assert conn.commit_count == 1

    def test_unsafe_tenant_id_rejected(self) -> None:
        manager = IsolationProfileManager()
        conn = FakeConnection()
        with pytest.raises(UnsafeTenantIdentifierError):
            manager.provision("'; DROP TABLE tenants; --", IsolationProfile.DEDICATED_SCHEMA, conn)


class TestTenantProvisioner:
    def test_tenant_provisioner_creates_kek(self) -> None:
        """Activation -> KMS key created (required named test)."""
        kms = FakeKMSClient()
        provisioner = TenantProvisioner(user_repository=_FakeUserRepository(), kms_client=kms)
        tenant_id = TenantId(str(uuid.uuid4()))

        result = provisioner.provision(tenant_id, "admin@acme.example", "Acme Admin")

        assert result.kek_id == f"tenant-{tenant_id}"
        assert result.kek_id in kms._keks

    def test_provisioner_creates_redis_namespace_marker(self) -> None:
        redis = FakeRedisClient()
        provisioner = TenantProvisioner(user_repository=_FakeUserRepository(), redis=redis)
        tenant_id = TenantId(str(uuid.uuid4()))

        result = provisioner.provision(tenant_id, "admin@acme.example", "Acme Admin")

        assert redis.get(result.redis_namespace_key) is not None

    def test_provisioner_creates_default_admin(self) -> None:
        users = _FakeUserRepository()
        provisioner = TenantProvisioner(user_repository=users)
        tenant_id = TenantId(str(uuid.uuid4()))

        result = provisioner.provision(tenant_id, "admin@acme.example", "Acme Admin")

        assert result.admin_user.email == "admin@acme.example"
        assert result.admin_role.name == "ADMIN"
        assert len(users.users) == 1
        assert len(users.assignments) == 1
        assert users.assignments[0].org_scope.scope_type == "TENANT"

    def test_provisioner_emits_tenant_provisioned_event(self) -> None:
        bus = EventBus(FakeRedisClient())
        publisher = Publisher(bus)
        provisioner = TenantProvisioner(user_repository=_FakeUserRepository(), publisher=publisher)
        tenant_id = TenantId(str(uuid.uuid4()))

        result = provisioner.provision(tenant_id, "admin@acme.example", "Acme Admin")

        assert result.event_published is True
        entries = bus.replay_from()
        assert any(envelope.event_type == TENANT_PROVISIONED_EVENT_TYPE for _entry_id, envelope in entries)

    def test_provisioner_records_audit(self) -> None:
        audit_repo = _StubAuditRepository()
        logger = AuditLogger(audit_repo)
        provisioner = TenantProvisioner(user_repository=_FakeUserRepository(), audit_logger=logger)
        tenant_id = TenantId(str(uuid.uuid4()))

        provisioner.provision(tenant_id, "admin@acme.example", "Acme Admin")

        assert len(audit_repo.calls) == 1
        assert audit_repo.calls[0]["action"] == "tenant.provisioned"


class TestTenantSuspender:
    def test_suspend_blocks_call_admission(self) -> None:
        repo = _FakeTenantRepository()
        tenant = repo.create(_make_tenant(TenantStatus.PRODUCTION))
        suspender = TenantSuspender(repo)

        assert suspender.is_call_admission_allowed(tenant.tenant_id) is True
        suspender.suspend(tenant.tenant_id)
        assert suspender.is_call_admission_allowed(tenant.tenant_id) is False

    def test_reactivate_restores_call_admission(self) -> None:
        repo = _FakeTenantRepository()
        tenant = repo.create(_make_tenant(TenantStatus.PRODUCTION))
        suspender = TenantSuspender(repo)

        suspender.suspend(tenant.tenant_id)
        suspender.reactivate(tenant.tenant_id)
        assert suspender.is_call_admission_allowed(tenant.tenant_id) is True

    def test_suspend_from_trial_is_invalid(self) -> None:
        repo = _FakeTenantRepository()
        tenant = repo.create(_make_tenant(TenantStatus.TRIAL))
        suspender = TenantSuspender(repo)

        with pytest.raises(InvalidTenantTransitionError):
            suspender.suspend(tenant.tenant_id)


class TestTenantDeleter:
    def test_delete_workflow_crypto_shreds_kek(self) -> None:
        repo = _FakeTenantRepository()
        tenant = repo.create(_make_tenant(TenantStatus.CANCELLED))
        kms = FakeKMSClient()
        kek_id = f"tenant-{tenant.tenant_id}"
        kms.ensure_kek(kek_id)
        deleter = TenantDeleter(repo, kms_client=kms)

        deleter.begin_deletion(tenant.tenant_id)
        after_begin = repo.get(tenant.tenant_id)
        assert after_begin is not None
        assert after_begin.status is TenantStatus.DELETING

        deleter.complete_deletion(tenant.tenant_id)
        after_complete = repo.get(tenant.tenant_id)
        assert after_complete is not None
        assert after_complete.status is TenantStatus.DELETED
        assert kek_id not in kms._keks


class TestTenantService:
    def test_create_tenant_starts_in_trial(self) -> None:
        service = TenantService(_FakeTenantRepository())
        tenant = service.create_tenant("acme-collections", "Acme Collections", "GROWTH")
        assert tenant.status is TenantStatus.TRIAL

    def test_activate_production_runs_provisioning(self) -> None:
        repo = _FakeTenantRepository()
        users = _FakeUserRepository()
        service = TenantService(repo, provisioner=TenantProvisioner(user_repository=users))

        tenant = service.create_tenant("acme-collections", "Acme Collections", "GROWTH")
        service.move_to_sandbox(tenant.tenant_id)
        activated, result = service.activate_production(tenant.tenant_id, "admin@acme.example", "Acme Admin")

        assert activated.status is TenantStatus.PRODUCTION
        assert result.admin_user.email == "admin@acme.example"

    def test_invalid_transition_raises_before_provisioning_runs(self) -> None:
        repo = _FakeTenantRepository()
        users = _FakeUserRepository()
        service = TenantService(repo, provisioner=TenantProvisioner(user_repository=users))
        tenant = service.create_tenant("acme-collections", "Acme Collections", "GROWTH")

        with pytest.raises(InvalidTenantTransitionError):
            # skips SANDBOX — invalid
            service.activate_production(tenant.tenant_id, "admin@acme.example", "Acme Admin")
        assert users.users == []
