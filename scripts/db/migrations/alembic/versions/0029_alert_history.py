"""ADR-006 Sec 4/7: alert_history (plumbing/ alert lifecycle)

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-25

Owned by ``src/services/ops_intelligence/plumbing/`` -- the always-on,
non-AI sub-component (ADR-006 Sec 3.0). This table's writes must succeed
even when ``reasoning/`` (and any LLM dependency) is fully down; nothing in
this migration or the repository built on top of it may depend on the
``ops_intelligence.reasoning`` package.

``source`` distinguishes the two existing, independent alert-producing
mechanisms this table reconciles without creating a third (ADR-006 Sec 4/
13.4): Prometheus/Alertmanager-triggered alerts, and
``compliance_monitoring.ComplianceAlerter``'s event-bus-published signals.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_SOURCES = ("alertmanager", "compliance_monitoring")
_STATUSES = ("firing", "acknowledged", "escalated", "resolved")


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS alert_history (
            alert_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID        REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            source          TEXT        NOT NULL CHECK (source IN {_SOURCES}),
            fingerprint     TEXT        NOT NULL,
            severity        TEXT        NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
            status          TEXT        NOT NULL DEFAULT 'firing' CHECK (status IN {_STATUSES}),
            fired_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            acknowledged_at TIMESTAMPTZ,
            acknowledged_by TEXT,
            escalated_at    TIMESTAMPTZ,
            escalated_to    TEXT,
            resolved_at     TIMESTAMPTZ,
            labels          JSONB       NOT NULL DEFAULT '{{}}',
            annotations     JSONB       NOT NULL DEFAULT '{{}}'
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_alert_history_tenant_fired ON alert_history (tenant_id, fired_at DESC);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_alert_history_status ON alert_history (status) WHERE status != 'resolved';")
    op.execute("CREATE INDEX IF NOT EXISTS idx_alert_history_fingerprint ON alert_history (fingerprint, fired_at DESC);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS alert_history CASCADE;")
