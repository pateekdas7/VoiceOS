"""ADR-006 Sec 3.2.2/7: ops_insights (Intelligent Analysis Layer output)

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-25

ADR-006 scope only -- chains directly from 0027 (the last pre-ADR-005/006
migration). Renumbered from an earlier draft that also included ADR-005
Actor Model/Pipeline/VoiceProfile/Notifications migrations (0028-0031);
those were removed from this branch since ADR-005's frontend/platform work
(including its own DB schema) is being implemented in a separate session.

Stores every AI-generated ``Insight`` (ADR-006 Sec 3.2.2) produced by
``src/services/ops_intelligence/reasoning/``. ``verified_facts`` is never
empty for a stored row -- that invariant is enforced in
``reasoning/insight_service.py`` before INSERT, not at the DB layer, since
Postgres CHECK constraints cannot easily validate "jsonb array has >= 1
element" portably across the value's possible shapes; the application-layer
gate is the actual enforcement point (ADR-006 Sec 3.2.2).

``tenant_id`` NULL = platform-wide insight; non-NULL = tenant-scoped,
following the same nullable-for-platform-wide convention every new
ADR-006 table uses (Sec 7/10).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_CATEGORIES = ("anomaly", "regression", "rca", "prediction", "summary", "domain_quality")
_SEVERITIES = ("info", "warning", "critical")
_CONFIDENCE_LEVELS = ("low", "medium", "high")


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS ops_insights (
            insight_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID        REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            category            TEXT        NOT NULL CHECK (category IN {_CATEGORIES}),
            severity            TEXT        NOT NULL CHECK (severity IN {_SEVERITIES}),
            verified_facts      JSONB       NOT NULL DEFAULT '[]',
            hypotheses          JSONB       NOT NULL DEFAULT '[]',
            recommendation      TEXT,
            affected_components JSONB      NOT NULL DEFAULT '[]',
            confidence_level    TEXT        NOT NULL CHECK (confidence_level IN {_CONFIDENCE_LEVELS}),
            model               TEXT        NOT NULL,
            prompt_version      TEXT        NOT NULL,
            generated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reviewed_by         TEXT,
            reviewed_at         TIMESTAMPTZ
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_ops_insights_tenant_generated ON ops_insights (tenant_id, generated_at DESC);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ops_insights_category_severity ON ops_insights (category, severity);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ops_insights_generated_at ON ops_insights (generated_at DESC);")

    # Defense-in-depth for the Sec 3.2.2 "never store an Insight with zero
    # verified_facts" rule -- the application layer is the real enforcement
    # point, but a CHECK backstops it the same way audit_log's immutability
    # trigger backstops AuditRepository's own no-update/no-delete contract.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_ops_insights_has_verified_facts'
            ) THEN
                ALTER TABLE ops_insights
                    ADD CONSTRAINT ck_ops_insights_has_verified_facts
                    CHECK (jsonb_array_length(verified_facts) >= 1);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ops_insights CASCADE;")
