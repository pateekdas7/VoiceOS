"""TenantDataMigration -- durable, idempotent, per-tenant-locked data migrations (V5 Ch23).

``run_migration(migration_id, tenant_id)`` runs a specific migration exactly
once per ``(tenant_id, migration_id)`` pair: idempotent (the pair is a claim
key against ``tenant_migrations``, mirroring the ``IdempotencyGuard``
claim-then-complete pattern from Sprint-015, but against a dedicated,
richer-shaped log table rather than the generic ``idempotency_keys`` store,
since a migration run has its own status lifecycle and mandatory rollback
procedure, not just an opaque cached JSON result) and locked (a
``DistributedLock`` on ``migration:{tenant_id}`` prevents two concurrent
migration runs for the same tenant -- the lock is optional, same
"None preserves prior behavior" precedent as every other Redis-backed
collaborator in this codebase).

Every registered migration must declare a ``rollback()`` procedure
(``TenantMigrationDefinition`` Protocol) -- this is enforced structurally,
not just documented: a migration definition without a ``rollback`` method
cannot satisfy the Protocol.

Architecture: V5 Ch23 (SaaS Operations Platform -- Tenant Data Migration Framework).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from src.libs.contracts.models.saas_ops import TenantMigrationRecord, TenantMigrationStatus
from src.libs.contracts.primitives import TenantId
from src.libs.redis_client.lock import DistributedLock

from . import metrics

MigrationResult = TenantMigrationRecord
"""Alias for Sprint-026.md's literal ``MigrationResult`` return type."""


class TenantMigrationAlreadyRunningError(RuntimeError):
    """Raised when a migration lock for a tenant is already held (concurrent-run guard)."""


class TenantMigrationDefinition(Protocol):
    """A single, named migration. Every migration must declare a rollback procedure."""

    migration_id: str

    def run(self, tenant_id: TenantId) -> str:
        """Apply the migration for ``tenant_id``. Returns a human-readable detail string."""
        ...

    def rollback(self, tenant_id: TenantId) -> None:
        """Reverse the migration's effect for ``tenant_id``."""
        ...


class TenantMigrationRepositoryPort(Protocol):
    def claim_running(self, tenant_id: TenantId, migration_id: str) -> bool: ...
    def get(self, tenant_id: TenantId, migration_id: str) -> TenantMigrationRecord | None: ...
    def mark_status(
        self,
        tenant_id: TenantId,
        migration_id: str,
        status: TenantMigrationStatus,
        detail: str,
        completed_at: datetime | None,
    ) -> None: ...


class TenantDataMigration:
    """Runs registered, per-tenant-locked, idempotent data migrations."""

    def __init__(self, repository: TenantMigrationRepositoryPort, redis: Any | None = None) -> None:
        self._repo = repository
        self._lock = DistributedLock(redis) if redis is not None else None

    def run_migration(self, migration: TenantMigrationDefinition, tenant_id: TenantId) -> MigrationResult:
        """Run ``migration`` for ``tenant_id`` exactly once.

        Idempotent: calling this twice with the same ``migration.migration_id``
        and ``tenant_id`` runs the effect only on the first call; the second
        call returns the already-recorded result without re-executing.

        Raises:
            TenantMigrationAlreadyRunningError: if a Redis client was supplied
                and another concurrent call already holds the per-tenant
                migration lock.
        """
        existing = self._repo.get(tenant_id, migration.migration_id)
        if existing is not None:
            metrics.record_migration_run(migration.migration_id, cache_hit=True)
            return existing

        token = None
        if self._lock is not None:
            token = self._lock.acquire(f"migration:{tenant_id}", owner_id=migration.migration_id)
            if token is None:
                raise TenantMigrationAlreadyRunningError(f"a migration is already running for tenant {tenant_id!r}")

        try:
            claimed = self._repo.claim_running(tenant_id, migration.migration_id)
            if not claimed:
                # Lost the claim race to a concurrent caller (no lock supplied,
                # or the claim table itself is the sole idempotency boundary).
                result = self._repo.get(tenant_id, migration.migration_id)
                assert result is not None
                metrics.record_migration_run(migration.migration_id, cache_hit=True)
                return result

            try:
                detail = migration.run(tenant_id)
            except Exception as exc:
                self._repo.mark_status(
                    tenant_id, migration.migration_id, TenantMigrationStatus.FAILED, str(exc), datetime.now(UTC)
                )
                metrics.record_migration_run(migration.migration_id, cache_hit=False, failed=True)
                raise

            self._repo.mark_status(
                tenant_id, migration.migration_id, TenantMigrationStatus.COMPLETED, detail, datetime.now(UTC)
            )
            metrics.record_migration_run(migration.migration_id, cache_hit=False)
            result = self._repo.get(tenant_id, migration.migration_id)
            assert result is not None
            return result
        finally:
            if self._lock is not None and token is not None:
                self._lock.release(token)

    def rollback_migration(self, migration: TenantMigrationDefinition, tenant_id: TenantId) -> None:
        """Invoke ``migration.rollback()`` and mark the run ROLLED_BACK."""
        migration.rollback(tenant_id)
        self._repo.mark_status(
            tenant_id, migration.migration_id, TenantMigrationStatus.ROLLED_BACK, "rolled back", datetime.now(UTC)
        )


__all__ = [
    "MigrationResult",
    "TenantDataMigration",
    "TenantMigrationAlreadyRunningError",
    "TenantMigrationDefinition",
    "TenantMigrationRepositoryPort",
]
