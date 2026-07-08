"""Sprint-026: SaaS Operations Platform -- feature_flags, tenant_rollout_rings,
fleet_versions, tenant_migrations (V5 Ch23).

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-07

Additive only -- no existing table is altered or dropped. This migration adds:

  * ``feature_flags`` -- one row per targeting scope (GLOBAL/PLAN/COHORT/
    TENANT) for a named flag. Deliberately not a single-row-per-flag table:
    ``FeatureFlagService.is_enabled()`` resolves the *most specific* matching
    row across scopes (V5 Ch23: "global -> plan-level -> cohort ->
    per-tenant"), so each scope is its own row, uniquely keyed by
    ``(flag_name, scope, scope_value)``. ``scope_value`` is nullable only for
    the GLOBAL scope (a NULL-safe unique index, since plain UNIQUE treats
    NULLs as distinct in Postgres, which is exactly the desired "at most one
    GLOBAL row per flag_name" semantics here).
  * ``tenant_rollout_rings`` -- sticky, idempotent per-tenant ring assignment
    (1=canary .. 4=laggard). Persisted rather than recomputed on every call
    so a tenant's ring never silently shifts if the assignment algorithm
    changes later.
  * ``fleet_versions`` -- the current target version promoted to each ring
    (one row per ring, 1..4).
  * ``tenant_migrations`` -- durable per-tenant migration run log.
    ``migration_id`` is the idempotency key (unique per tenant): a
    COMPLETED row for ``(tenant_id, migration_id)`` means
    ``TenantDataMigration.run_migration()`` must never re-execute the
    effect for that pair.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # feature_flags -- one row per targeting scope
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS feature_flags (
            flag_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            flag_name           TEXT        NOT NULL,
            scope               TEXT        NOT NULL CHECK (scope IN ('GLOBAL', 'PLAN', 'COHORT', 'TENANT')),
            scope_value         TEXT,
            state               TEXT        NOT NULL DEFAULT 'DISABLED'
                                             CHECK (state IN ('ENABLED', 'DISABLED', 'GRADUAL_ROLLOUT')),
            rollout_percentage  DOUBLE PRECISION NOT NULL DEFAULT 0
                                             CHECK (rollout_percentage >= 0 AND rollout_percentage <= 100),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT chk_feature_flags_global_scope_value
                CHECK ((scope = 'GLOBAL' AND scope_value IS NULL) OR (scope != 'GLOBAL' AND scope_value IS NOT NULL))
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_feature_flags_global ON feature_flags (flag_name) WHERE scope = 'GLOBAL';"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_feature_flags_scoped "
        "ON feature_flags (flag_name, scope, scope_value) WHERE scope != 'GLOBAL';"
    )

    # ------------------------------------------------------------------
    # tenant_rollout_rings -- sticky per-tenant ring assignment
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_rollout_rings (
            tenant_id           UUID        PRIMARY KEY REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            ring                INT         NOT NULL CHECK (ring BETWEEN 1 AND 4),
            assigned_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_tenant_rollout_rings_ring ON tenant_rollout_rings (ring);")

    # ------------------------------------------------------------------
    # fleet_versions -- current target version per ring
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fleet_versions (
            ring                INT         PRIMARY KEY CHECK (ring BETWEEN 1 AND 4),
            version             TEXT        NOT NULL,
            promoted_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )

    # ------------------------------------------------------------------
    # tenant_migrations -- durable, idempotent per-tenant migration log
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_migrations (
            tenant_id           UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            migration_id        TEXT        NOT NULL,
            status              TEXT        NOT NULL
                                             CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED', 'ROLLED_BACK')),
            detail              TEXT        NOT NULL DEFAULT '',
            started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at        TIMESTAMPTZ,
            PRIMARY KEY (tenant_id, migration_id)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_tenant_migrations_status ON tenant_migrations (tenant_id, status);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tenant_migrations CASCADE;")
    op.execute("DROP TABLE IF EXISTS fleet_versions CASCADE;")
    op.execute("DROP TABLE IF EXISTS tenant_rollout_rings CASCADE;")
    op.execute("DROP TABLE IF EXISTS feature_flags CASCADE;")
