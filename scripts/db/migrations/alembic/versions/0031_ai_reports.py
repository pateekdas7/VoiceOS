"""ADR-006 Sec 6/7: ai_reports (AIReportGenerator output, 9-type catalog)

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-25

Every report type in ADR-006 Sec 6.1 (executive_summary, engineering_summary,
incident_report, rca, performance_report, capacity_report, daily_health,
weekly_health, monthly_health) shares this one schema, deliberately -- so
every report is machine-readable/diffable/auditable the same way and the
UI needs only one renderer (ADR-006 Sec 6.2). ``source_service_calls``
(added in Rev 3, Sec 6.2/13.8) carries frozen snapshots of the pre-existing
``BIPlatformService``/``OpsAnalytics`` calls a report's KPI numbers came
from, so a report is reproducible even for the numeric content that isn't
backed by an ``ops_insights`` row.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_REPORT_TYPES = (
    "executive_summary",
    "engineering_summary",
    "incident_report",
    "rca",
    "performance_report",
    "capacity_report",
    "daily_health",
    "weekly_health",
    "monthly_health",
)
_SCOPE_LEVELS = ("platform", "tenant")


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS ai_reports (
            report_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            report_type          TEXT        NOT NULL CHECK (report_type IN {_REPORT_TYPES}),
            period_start         TIMESTAMPTZ NOT NULL,
            period_end           TIMESTAMPTZ NOT NULL,
            scope_level          TEXT        NOT NULL CHECK (scope_level IN {_SCOPE_LEVELS}),
            tenant_id            UUID        REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            severity             TEXT        NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
            affected_components  JSONB       NOT NULL DEFAULT '[]',
            business_impact      TEXT        NOT NULL,
            recommended_actions  JSONB       NOT NULL DEFAULT '[]',
            confidence_level     TEXT        NOT NULL CHECK (confidence_level IN ('low', 'medium', 'high')),
            evidence             JSONB       NOT NULL DEFAULT '[]',
            source_service_calls JSONB       NOT NULL DEFAULT '[]',
            narrative            TEXT        NOT NULL,
            generated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            delivered_to         JSONB       NOT NULL DEFAULT '[]',

            CONSTRAINT ck_ai_reports_scope_tenant CHECK (
                (scope_level = 'platform' AND tenant_id IS NULL) OR
                (scope_level = 'tenant' AND tenant_id IS NOT NULL)
            )
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_ai_reports_tenant_type_period ON ai_reports (tenant_id, report_type, period_start DESC);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ai_reports_scope_level ON ai_reports (scope_level, generated_at DESC);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_reports CASCADE;")
