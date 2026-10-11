"""Add call_attempts table for durable pre-call records.

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-16

Creates the call_attempts table written before every Twilio call is initiated.
Enables crash reconciliation (Phase 3) by providing a durable record of every
attempt even when the dialer_worker crashes between Twilio initiation and the
active_calls write.

Rollback removes the table completely — safe because no other table references
call_attempts as a foreign key source.
"""

from __future__ import annotations

from collections.abc import Sequence

import alembic.op as op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS call_attempts (
            attempt_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            campaign_id     UUID        NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
            lead_id         UUID        NOT NULL,
            pipeline_id     UUID,
            call_sid        TEXT        UNIQUE,
            worker_id       TEXT        NOT NULL,
            status          TEXT        NOT NULL DEFAULT 'INITIATED'
                            CHECK (status IN ('INITIATED','IN_PROGRESS','COMPLETED',
                                              'FAILED','TIMEOUT','NO_ANSWER','BUSY')),
            disposition     TEXT,
            duration_s      INTEGER     CHECK (duration_s IS NULL OR duration_s >= 0),
            initiated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            answered_at     TIMESTAMPTZ,
            ended_at        TIMESTAMPTZ,
            error_message   TEXT
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_call_attempts_open
            ON call_attempts (worker_id, status, initiated_at)
            WHERE status IN ('INITIATED', 'IN_PROGRESS')
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_call_attempts_lead ON call_attempts (lead_id, initiated_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_call_attempts_tenant ON call_attempts (tenant_id, initiated_at DESC)")
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_call_attempts_sid
            ON call_attempts (call_sid) WHERE call_sid IS NOT NULL
    """)
    op.execute("GRANT ALL PRIVILEGES ON TABLE call_attempts TO voiceos")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS call_attempts")
