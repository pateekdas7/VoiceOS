"""Sprint-015: snapshots, recovery_log

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-04

New tables (additive — no changes to existing schema):
  - ``snapshots``: periodic Recoverable-state serialization (V3 Ch6). One row
    per (call_id, version); the highest version per call_id is the latest
    restorable checkpoint.
  - ``recovery_log``: auditable history of every crash-recovery attempt
    (V3 Ch7) — which failure class, which strategy, success/failure, timing.

``tenant_id`` is deliberately NOT a FK to ``tenants`` here, matching the
convention already used by every other per-call/per-customer operational
table (``customers``, ``loan_accounts``, ``promises_to_pay``,
``idempotency_keys``, ``audit_log``) — tenant isolation for these is enforced
at the repository query layer (``BaseRepository``), not via a DB constraint.
Only structural/org-hierarchy tables (``organizations``, ``users``,
``campaigns``, ``billing_subscriptions``, ``usage_events``) FK to ``tenants``.
Snapshots/recovery_log are Tier-1 reliability primitives (BUILD_ORDER.md) and
must not hard-depend on the Tier-7 tenant-management table, which does not
exist functionally until Sprint-021.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from scripts.db.migrations.alembic._ddl_helpers import add_enum_check, drop_check

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id                  BIGSERIAL   PRIMARY KEY,
            tenant_id           UUID        NOT NULL,
            call_id             TEXT        NOT NULL,
            version             INTEGER     NOT NULL CHECK (version >= 1),
            state               JSONB       NOT NULL,
            last_event_offset   TEXT        NOT NULL DEFAULT '-',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (call_id, version)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_tenant_id ON snapshots (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_call_latest ON snapshots (call_id, version DESC);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS recovery_log (
            id              BIGSERIAL   PRIMARY KEY,
            tenant_id       UUID        NOT NULL,
            call_id         TEXT        NOT NULL,
            failure_class   TEXT        NOT NULL,
            strategy_name   TEXT        NOT NULL,
            outcome         TEXT        NOT NULL,
            detail          JSONB       NOT NULL DEFAULT '{}'::jsonb,
            started_at      TIMESTAMPTZ NOT NULL,
            completed_at    TIMESTAMPTZ NOT NULL,
            duration_ms     INTEGER     NOT NULL CHECK (duration_ms >= 0),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_recovery_log_tenant_id ON recovery_log (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_recovery_log_call_id ON recovery_log (call_id, created_at DESC);")

    add_enum_check("recovery_log", "ck_recovery_log_outcome_enum", "outcome", ("success", "failure"))


def downgrade() -> None:
    drop_check("recovery_log", "ck_recovery_log_outcome_enum")
    op.execute("DROP TABLE IF EXISTS recovery_log CASCADE;")
    op.execute("DROP TABLE IF EXISTS snapshots CASCADE;")
