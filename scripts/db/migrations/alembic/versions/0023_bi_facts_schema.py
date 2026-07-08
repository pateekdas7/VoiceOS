"""Sprint-024: bi_facts schema — BIWarehouse dedicated star schema (V5 Ch21)

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-06

A dedicated Postgres *schema* (not just a table), per Sprint-024.md: "Stores
aggregates in a dedicated bi_facts schema in Postgres (separate from
operational tables)".

- ``bi_facts.dim_tenant``: a surrogate-key dimension mapping real
  ``tenant_id`` -> an opaque ``tenant_surrogate_key``. ``CrossTenantBenchmarking``
  queries only ever join/group on the surrogate key, never on ``tenant_id``
  directly, which is what makes "no tenant identifiers exposed" (Sprint-024
  AC) mechanically true rather than an application-layer promise.
- ``bi_facts.fact_daily``: the daily-refreshed fact table BIWarehouse.refresh()
  populates from AnalyticsService (ptp_rate/recovery_rate), BillingService
  (revenue_minor), MeteringService (usage_* columns), and ComplianceMonitoring
  (compliance_score).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS bi_facts;")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bi_facts.dim_tenant (
            tenant_surrogate_key    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL UNIQUE REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bi_facts.fact_daily (
            fact_daily_id           UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_surrogate_key    UUID             NOT NULL
                REFERENCES bi_facts.dim_tenant (tenant_surrogate_key) ON DELETE CASCADE,
            day                     DATE             NOT NULL,
            revenue_minor           BIGINT           NOT NULL DEFAULT 0 CHECK (revenue_minor >= 0),
            usage_call_minutes      BIGINT           NOT NULL DEFAULT 0 CHECK (usage_call_minutes >= 0),
            usage_stt_tokens        BIGINT           NOT NULL DEFAULT 0 CHECK (usage_stt_tokens >= 0),
            usage_llm_tokens        BIGINT           NOT NULL DEFAULT 0 CHECK (usage_llm_tokens >= 0),
            usage_gpu_seconds       BIGINT           NOT NULL DEFAULT 0 CHECK (usage_gpu_seconds >= 0),
            ptp_rate                DOUBLE PRECISION NOT NULL DEFAULT 0,
            recovery_rate           DOUBLE PRECISION NOT NULL DEFAULT 0,
            compliance_score        DOUBLE PRECISION NOT NULL DEFAULT 0,
            refreshed_at            TIMESTAMPTZ      NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_bi_fact_daily_tenant_day "
        "ON bi_facts.fact_daily (tenant_surrogate_key, day);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_bi_fact_daily_day ON bi_facts.fact_daily (day);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bi_facts.fact_daily CASCADE;")
    op.execute("DROP TABLE IF EXISTS bi_facts.dim_tenant CASCADE;")
    op.execute("DROP SCHEMA IF EXISTS bi_facts CASCADE;")
