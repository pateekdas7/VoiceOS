"""Sprint-028: performance_baselines — per-stage p50/p95/p99 latency baselines
stored by ContinuousProfiler for regression detection (V3 Ch19).

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-03

Additive only — no existing table is altered or dropped. This migration adds:

  * ``performance_baselines`` — one row per (stage, measured_date).
    ``stage`` is the pipeline stage identifier (e.g. 'stt', 'llm_ttft').
    ``measured_date`` is the UTC calendar date the samples were collected.
    The (stage, measured_date) pair is the natural unique key: ContinuousProfiler
    writes one row per stage per day via ``flush()``. The ``RegressionDetector``
    reads the latest row for each stage to establish the allowed threshold.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS performance_baselines (
            id              BIGSERIAL PRIMARY KEY,
            stage           TEXT        NOT NULL,
            measured_date   DATE        NOT NULL,
            p50_ms          DOUBLE PRECISION NOT NULL,
            p95_ms          DOUBLE PRECISION NOT NULL,
            p99_ms          DOUBLE PRECISION NOT NULL,
            recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_performance_baselines_stage_date
                UNIQUE (stage, measured_date)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_performance_baselines_stage_date
            ON performance_baselines (stage, measured_date DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS performance_baselines")
