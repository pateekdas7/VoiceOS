"""Add compliance_violations table for durable violation lifecycle tracking.

Revision ID: 0037
Revises: 0036
Create Date: 2026-09-17

Replaces the in-memory _violated_tenants set in ComplianceMonitoring with a
durable Postgres table so violation state survives service restarts. One row per
(tenant_id, rule_id); lifecycle: ACTIVE → RESOLVED → ACTIVE (re-detected).

Rollback drops the table — safe because no other table references it as a FK source.
"""

from __future__ import annotations

from collections.abc import Sequence

import alembic.op as op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS compliance_violations (
            violation_id    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            rule_id         TEXT        NOT NULL,
            signal_summary  TEXT        NOT NULL DEFAULT '',
            status          TEXT        NOT NULL DEFAULT 'ACTIVE'
                            CHECK (status IN ('ACTIVE', 'RESOLVED')),
            detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at     TIMESTAMPTZ,
            redetected_at   TIMESTAMPTZ,
            CONSTRAINT uq_compliance_violations_tenant_rule UNIQUE (tenant_id, rule_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_compliance_violations_active
            ON compliance_violations (tenant_id, status)
            WHERE status = 'ACTIVE'
    """)
    op.execute("GRANT ALL PRIVILEGES ON TABLE compliance_violations TO voiceos")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS compliance_violations")
