"""Sprint-014: audit_log (006_audit_log.sql) + DB-level immutability trigger

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-04

AuditRepository enforces append-only at the Python layer (no update()/delete()
method — see src/libs/repositories/audit.py). This migration adds a
defense-in-depth database trigger so immutability holds even against direct
SQL access, matching V4 Ch11 "tamper-evident" audit trail requirement and the
existing 006_audit_log.sql comment ("no UPDATE or DELETE permitted").
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            audit_id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL,
            actor_id                TEXT        NOT NULL,
            action                  TEXT        NOT NULL,
            resource_type           TEXT        NOT NULL,
            resource_id             TEXT        NOT NULL,
            outcome                 TEXT        NOT NULL,
            ip_address              TEXT        NOT NULL DEFAULT '',
            event_payload           JSONB,
            recorded_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_tenant_id ON audit_log (tenant_id, recorded_at DESC);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor_id ON audit_log (actor_id, recorded_at DESC);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log (resource_type, resource_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log (action, recorded_at DESC);")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only: % is not permitted (V4 Ch11)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log;")
    op.execute(
        """
        CREATE TRIGGER trg_audit_log_immutable
            BEFORE UPDATE OR DELETE ON audit_log
            FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log;")
    op.execute("DROP FUNCTION IF EXISTS audit_log_immutable();")
    op.execute("DROP TABLE IF EXISTS audit_log CASCADE;")
