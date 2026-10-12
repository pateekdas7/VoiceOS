"""Sprint-028: performance_baselines (ContinuousProfiler daily per-stage percentiles, V3 Ch19)

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-09

One row per (stage_name, environment, recorded_date) -- ContinuousProfiler's
``flush_daily_percentiles()`` upserts here once per stage per day. Not
tenant-scoped: this is fleet-level operational data (pipeline-stage
latency), not customer data, so it deliberately does not follow the
mechanically tenant-scoped ``BaseRepository`` pattern every domain
repository since Sprint-014 has used.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS performance_baselines (
            performance_baseline_id UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
            stage_name              TEXT             NOT NULL,
            environment              TEXT             NOT NULL DEFAULT 'production',
            recorded_date            DATE             NOT NULL,
            p50_ms                   DOUBLE PRECISION NOT NULL CHECK (p50_ms >= 0),
            p95_ms                   DOUBLE PRECISION NOT NULL CHECK (p95_ms >= 0),
            p99_ms                   DOUBLE PRECISION NOT NULL CHECK (p99_ms >= 0),
            sample_count             BIGINT           NOT NULL CHECK (sample_count > 0),
            created_at               TIMESTAMPTZ      NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_performance_baselines_stage_env_date "
        "ON performance_baselines (stage_name, environment, recorded_date);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_performance_baselines_stage_date "
        "ON performance_baselines (stage_name, recorded_date DESC);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS performance_baselines CASCADE;")
