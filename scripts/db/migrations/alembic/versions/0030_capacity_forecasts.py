"""ADR-006 Sec 1.5/6/7: capacity_forecasts (CapacityPlanning service output)

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-25

Backs ``src/services/ops_intelligence/reasoning/capacity_planner.py``'s
implementation of Volume 7 Ch.12's previously-unimplemented
``forecast()``/``headroom()`` interface. Retained indefinitely (ADR-006
Sec 7): forecast accuracy is only reviewable in hindsight if past forecasts
are never pruned.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_RESOURCES = ("gpu", "cpu", "redis", "db", "queue")


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS capacity_forecasts (
            forecast_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID        REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            resource        TEXT        NOT NULL CHECK (resource IN {_RESOURCES}),
            horizon_days    INT         NOT NULL CHECK (horizon_days IN (30, 90, 365)),
            forecast_data   JSONB       NOT NULL DEFAULT '{{}}',
            headroom_pct    DOUBLE PRECISION NOT NULL CHECK (headroom_pct >= -100 AND headroom_pct <= 100),
            confidence      TEXT        NOT NULL CHECK (confidence IN ('low', 'medium', 'high')),
            generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_capacity_forecasts_resource ON capacity_forecasts (resource, generated_at DESC);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_capacity_forecasts_tenant ON capacity_forecasts (tenant_id, generated_at DESC);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS capacity_forecasts CASCADE;")
