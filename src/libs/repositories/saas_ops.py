"""FeatureFlagRepository, RolloutRepository, TenantMigrationRepository (V5 Ch23).

``FeatureFlagRepository``/``RolloutRepository`` deliberately do not subclass
``BaseRepository``: feature flags and fleet-version rows are not themselves
tenant-owned resources (a GLOBAL flag row and a ``fleet_versions`` row have
no ``tenant_id`` at all) -- same "not every table is tenant-scoped" precedent
as ``BIRepository`` (Sprint-024) for ``bi_facts``. ``TenantMigrationRepository``
*is* naturally tenant-scoped (every row is owned by exactly one tenant) and
subclasses ``BaseRepository`` as usual.

Architecture: V5 Ch23 (SaaS Operations Platform).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..contracts.models.saas_ops import (
    FeatureFlag,
    FeatureFlagScope,
    FeatureFlagState,
    FleetVersion,
    RolloutRing,
    TenantMigrationRecord,
    TenantMigrationStatus,
    TenantRolloutAssignment,
)
from ..contracts.primitives import TenantId
from .base import BaseRepository

_FEATURE_FLAGS_TABLE = "feature_flags"
_TENANT_ROLLOUT_RINGS_TABLE = "tenant_rollout_rings"
_FLEET_VERSIONS_TABLE = "fleet_versions"
_TENANT_MIGRATIONS_TABLE = "tenant_migrations"


class FeatureFlagRepository:
    """Queries against the ``feature_flags`` table (one row per targeting scope)."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def _cursor(self) -> Any:
        return self._conn.cursor()

    def _commit(self) -> None:
        self._conn.commit()

    def upsert(self, flag: FeatureFlag) -> FeatureFlag:
        cur = self._cursor()
        conflict_target = (
            "(flag_name) WHERE scope = 'GLOBAL'"
            if flag.scope == FeatureFlagScope.GLOBAL
            else "(flag_name, scope, scope_value) WHERE scope != 'GLOBAL'"
        )
        cur.execute(
            f"""
            INSERT INTO {_FEATURE_FLAGS_TABLE} (
                flag_id, flag_name, scope, scope_value, state, rollout_percentage,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT {conflict_target} DO UPDATE SET
                state = EXCLUDED.state,
                rollout_percentage = EXCLUDED.rollout_percentage,
                updated_at = EXCLUDED.updated_at
            """,
            (
                flag.flag_id,
                flag.flag_name,
                flag.scope.value,
                flag.scope_value,
                flag.state.value,
                flag.rollout_percentage,
                flag.created_at,
                flag.updated_at,
            ),
        )
        self._commit()
        return flag

    def get(self, flag_name: str, scope: FeatureFlagScope, scope_value: str | None) -> FeatureFlag | None:
        cur = self._cursor()
        if scope_value is None:
            cur.execute(
                f"SELECT * FROM {_FEATURE_FLAGS_TABLE} WHERE flag_name = %s AND scope = %s AND scope_value IS NULL",
                (flag_name, scope.value),
            )
        else:
            cur.execute(
                f"SELECT * FROM {_FEATURE_FLAGS_TABLE} WHERE flag_name = %s AND scope = %s AND scope_value = %s",
                (flag_name, scope.value, scope_value),
            )
        row = cur.fetchone()
        return self._hydrate(row) if row is not None else None

    def list_for_flag(self, flag_name: str) -> tuple[FeatureFlag, ...]:
        cur = self._cursor()
        cur.execute(f"SELECT * FROM {_FEATURE_FLAGS_TABLE} WHERE flag_name = %s", (flag_name,))
        return tuple(self._hydrate(row) for row in cur.fetchall())

    @staticmethod
    def _hydrate(row: tuple[Any, ...]) -> FeatureFlag:
        (
            flag_id,
            flag_name,
            scope,
            scope_value,
            state,
            rollout_percentage,
            created_at,
            updated_at,
        ) = row
        return FeatureFlag(
            flag_id=str(flag_id),
            flag_name=flag_name,
            scope=FeatureFlagScope(scope),
            scope_value=scope_value,
            state=FeatureFlagState(state),
            rollout_percentage=rollout_percentage,
            created_at=created_at,
            updated_at=updated_at,
        )


class RolloutRepository:
    """Queries against ``tenant_rollout_rings``/``fleet_versions`` (Fleet Rollout Manager)."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def _cursor(self) -> Any:
        return self._conn.cursor()

    def _commit(self) -> None:
        self._conn.commit()

    def get_ring(self, tenant_id: TenantId) -> TenantRolloutAssignment | None:
        cur = self._cursor()
        cur.execute(
            f"SELECT tenant_id, ring, assigned_at FROM {_TENANT_ROLLOUT_RINGS_TABLE} WHERE tenant_id = %s",
            (tenant_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        tid, ring, assigned_at = row
        return TenantRolloutAssignment(tenant_id=TenantId(str(tid)), ring=RolloutRing(ring), assigned_at=assigned_at)

    def assign_ring(self, tenant_id: TenantId, ring: RolloutRing) -> TenantRolloutAssignment:
        """Idempotently persist a tenant's ring assignment (first assignment wins -- sticky)."""
        cur = self._cursor()
        cur.execute(
            f"""
            INSERT INTO {_TENANT_ROLLOUT_RINGS_TABLE} (tenant_id, ring, assigned_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (tenant_id) DO NOTHING
            """,
            (tenant_id, int(ring)),
        )
        self._commit()
        existing = self.get_ring(tenant_id)
        assert existing is not None
        return existing

    def get_version_for_ring(self, ring: RolloutRing) -> FleetVersion | None:
        cur = self._cursor()
        cur.execute(f"SELECT ring, version, promoted_at FROM {_FLEET_VERSIONS_TABLE} WHERE ring = %s", (int(ring),))
        row = cur.fetchone()
        if row is None:
            return None
        ring_val, version, promoted_at = row
        return FleetVersion(ring=RolloutRing(ring_val), version=version, promoted_at=promoted_at)

    def promote_ring(self, ring: RolloutRing, version: str) -> FleetVersion:
        cur = self._cursor()
        cur.execute(
            f"""
            INSERT INTO {_FLEET_VERSIONS_TABLE} (ring, version, promoted_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (ring) DO UPDATE SET version = EXCLUDED.version, promoted_at = EXCLUDED.promoted_at
            """,
            (int(ring), version),
        )
        self._commit()
        result = self.get_version_for_ring(ring)
        assert result is not None
        return result


class TenantMigrationRepository(BaseRepository):
    """Tenant-scoped queries for the ``tenant_migrations`` domain (durable idempotency log)."""

    def claim_running(self, tenant_id: TenantId, migration_id: str) -> bool:
        """Attempt to claim ``(tenant_id, migration_id)`` as RUNNING.

        Returns True if this call won the claim (no prior row existed);
        False if a row already exists (COMPLETED, FAILED, RUNNING, or
        ROLLED_BACK) -- the caller must not re-execute the migration effect.
        """
        cur = self._execute(
            f"""
            INSERT INTO {_TENANT_MIGRATIONS_TABLE} (tenant_id, migration_id, status, started_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (tenant_id, migration_id) DO NOTHING
            """,
            (tenant_id, migration_id, TenantMigrationStatus.RUNNING.value),
        )
        self._commit()
        rowcount: int = cur.rowcount
        return rowcount == 1

    def get(self, tenant_id: TenantId, migration_id: str) -> TenantMigrationRecord | None:
        row = self._tenant_select_one(
            _TENANT_MIGRATIONS_TABLE,
            ("tenant_id", "migration_id", "status", "detail", "started_at", "completed_at"),
            tenant_id,
            extra_where="migration_id = %s",
            extra_params=(migration_id,),
        )
        return self._hydrate(row) if row is not None else None

    def mark_status(
        self,
        tenant_id: TenantId,
        migration_id: str,
        status: TenantMigrationStatus,
        detail: str,
        completed_at: datetime | None,
    ) -> None:
        self._tenant_update(
            _TENANT_MIGRATIONS_TABLE,
            ("status", "detail", "completed_at"),
            (status.value, detail, completed_at),
            tenant_id,
            extra_where="migration_id = %s",
            extra_params=(migration_id,),
        )

    @staticmethod
    def _hydrate(row: tuple[Any, ...]) -> TenantMigrationRecord:
        tenant_id, migration_id, status, detail, started_at, completed_at = row
        return TenantMigrationRecord(
            migration_id=migration_id,
            tenant_id=TenantId(str(tenant_id)),
            status=TenantMigrationStatus(status),
            detail=detail,
            started_at=started_at,
            completed_at=completed_at,
        )


__all__ = ["FeatureFlagRepository", "RolloutRepository", "TenantMigrationRepository"]
