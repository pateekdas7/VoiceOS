"""Unit tests for TenantDataMigration (Sprint-026, V5 Ch23).

Required named test (Sprint-026.md acceptance criteria):
    TenantDataMigration.run_migration() is idempotent (same migration_id
    twice -> one result, no double-apply).
"""

from __future__ import annotations

from datetime import datetime

from src.libs.contracts.models.saas_ops import TenantMigrationRecord, TenantMigrationStatus
from src.libs.contracts.primitives import TenantId
from src.services.saas_ops.migration import TenantDataMigration, TenantMigrationAlreadyRunningError
from tests.fixtures.redis import FakeRedisClient

_TENANT = TenantId("tenant-a")


class _FakeTenantMigrationRepository:
    def __init__(self) -> None:
        self._rows: dict[tuple[TenantId, str], TenantMigrationRecord] = {}

    def claim_running(self, tenant_id: TenantId, migration_id: str) -> bool:
        key = (tenant_id, migration_id)
        if key in self._rows:
            return False
        from datetime import UTC

        self._rows[key] = TenantMigrationRecord(
            migration_id=migration_id,
            tenant_id=tenant_id,
            status=TenantMigrationStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        return True

    def get(self, tenant_id: TenantId, migration_id: str) -> TenantMigrationRecord | None:
        return self._rows.get((tenant_id, migration_id))

    def mark_status(
        self,
        tenant_id: TenantId,
        migration_id: str,
        status: TenantMigrationStatus,
        detail: str,
        completed_at: datetime | None,
    ) -> None:
        existing = self._rows[(tenant_id, migration_id)]
        self._rows[(tenant_id, migration_id)] = existing.model_copy(
            update={"status": status, "detail": detail, "completed_at": completed_at}
        )


class _CountingMigration:
    """A migration definition that records how many times run()/rollback() fired."""

    migration_id = "backfill_customer_cohort"

    def __init__(self) -> None:
        self.run_count = 0
        self.rollback_count = 0

    def run(self, tenant_id: TenantId) -> str:
        self.run_count += 1
        return f"backfilled cohort for {tenant_id}"

    def rollback(self, tenant_id: TenantId) -> None:
        self.rollback_count += 1


class _FailingMigration:
    migration_id = "always_fails"

    def run(self, tenant_id: TenantId) -> str:
        raise RuntimeError("boom")

    def rollback(self, tenant_id: TenantId) -> None:
        pass


class TestTenantDataMigration:
    def test_run_migration_twice_same_migration_id_runs_effect_once(self) -> None:
        repo = _FakeTenantMigrationRepository()
        service = TenantDataMigration(repo)
        migration = _CountingMigration()

        first = service.run_migration(migration, _TENANT)
        second = service.run_migration(migration, _TENANT)

        assert migration.run_count == 1
        assert first.migration_id == second.migration_id
        assert first.status == TenantMigrationStatus.COMPLETED
        assert second.status == TenantMigrationStatus.COMPLETED

    def test_run_migration_different_tenants_run_independently(self) -> None:
        repo = _FakeTenantMigrationRepository()
        service = TenantDataMigration(repo)
        migration = _CountingMigration()

        service.run_migration(migration, TenantId("tenant-a"))
        service.run_migration(migration, TenantId("tenant-b"))

        assert migration.run_count == 2

    def test_run_migration_records_failure_and_reraises(self) -> None:
        repo = _FakeTenantMigrationRepository()
        service = TenantDataMigration(repo)
        migration = _FailingMigration()

        raised = False
        try:
            service.run_migration(migration, _TENANT)
        except RuntimeError:
            raised = True
        assert raised

        record = repo.get(_TENANT, migration.migration_id)
        assert record is not None
        assert record.status == TenantMigrationStatus.FAILED

    def test_rollback_migration_invokes_rollback_and_marks_rolled_back(self) -> None:
        repo = _FakeTenantMigrationRepository()
        service = TenantDataMigration(repo)
        migration = _CountingMigration()
        service.run_migration(migration, _TENANT)

        service.rollback_migration(migration, _TENANT)

        assert migration.rollback_count == 1
        record = repo.get(_TENANT, migration.migration_id)
        assert record is not None
        assert record.status == TenantMigrationStatus.ROLLED_BACK

    def test_concurrent_migration_for_same_tenant_raises_when_locked(self) -> None:
        repo = _FakeTenantMigrationRepository()
        redis = FakeRedisClient()
        service = TenantDataMigration(repo, redis=redis)
        migration = _CountingMigration()

        # Simulate a lock already held for this tenant by another caller.
        redis.set(f"voiceos:lock:migration:{_TENANT}", b"other-owner", ex=30)

        raised = False
        try:
            service.run_migration(migration, _TENANT)
        except TenantMigrationAlreadyRunningError:
            raised = True
        assert raised
        assert migration.run_count == 0

    def test_migration_lock_released_after_successful_run(self) -> None:
        repo = _FakeTenantMigrationRepository()
        redis = FakeRedisClient()
        service = TenantDataMigration(repo, redis=redis)
        migration = _CountingMigration()

        service.run_migration(migration, _TENANT)

        assert redis.get(f"voiceos:lock:migration:{_TENANT}") is None
