"""Sprint-024: analytics_daily (DailyAggregationJob rollup table, V5 Ch11)

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-06

One row per (tenant_id, day) for the tenant-wide rollup, plus one row per
(tenant_id, day, campaign_id) for each campaign's daily rollup —
``campaign_id IS NULL`` distinguishes the two. Two partial unique indexes
enforce at-most-one-row-per-key for each case (a plain UNIQUE over a
nullable column would treat every NULL as distinct in Postgres, permitting
duplicate tenant-wide rows for the same day).

Sourced from ``call_dispositions`` (migration 0011) and ``campaign_results``
(migration 0020) — both already carry the raw per-call/per-attempt facts
this rollup aggregates; no new raw-fact tables are needed.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics_daily (
            analytics_daily_id      UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID             NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            day                     DATE             NOT NULL,
            campaign_id             UUID             REFERENCES campaigns (campaign_id) ON DELETE CASCADE,
            calls_completed         BIGINT           NOT NULL DEFAULT 0 CHECK (calls_completed >= 0),
            ptp_count               BIGINT           NOT NULL DEFAULT 0 CHECK (ptp_count >= 0),
            ptp_rate                DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (ptp_rate >= 0 AND ptp_rate <= 1),
            avg_duration_ms         BIGINT           NOT NULL DEFAULT 0 CHECK (avg_duration_ms >= 0),
            amount_collected_minor  BIGINT           NOT NULL DEFAULT 0 CHECK (amount_collected_minor >= 0),
            contactability_rate     DOUBLE PRECISION NOT NULL DEFAULT 0,
            recovery_rate           DOUBLE PRECISION NOT NULL DEFAULT 0,
            avg_dpd                 DOUBLE PRECISION NOT NULL DEFAULT 0,
            computed_at             TIMESTAMPTZ      NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_analytics_daily_tenant_id ON analytics_daily (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_analytics_daily_day ON analytics_daily (tenant_id, day DESC);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_analytics_daily_campaign_id ON analytics_daily (campaign_id);")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_analytics_daily_tenant_day "
        "ON analytics_daily (tenant_id, day) WHERE campaign_id IS NULL;"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_analytics_daily_tenant_day_campaign "
        "ON analytics_daily (tenant_id, day, campaign_id) WHERE campaign_id IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS analytics_daily CASCADE;")
