"""0042 — support tickets + impersonation sessions (W17)

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-11
"""
from __future__ import annotations

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TYPE support_ticket_status AS ENUM (
            'OPEN', 'IN_PROGRESS', 'WAITING_ON_CUSTOMER', 'RESOLVED', 'CLOSED'
        )
    """)
    op.execute("""
        CREATE TYPE support_ticket_priority AS ENUM ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
    """)
    op.execute("""
        CREATE TABLE support_tickets (
            id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            tenant_id       TEXT NOT NULL,
            subject         TEXT NOT NULL,
            description     TEXT NOT NULL,
            status          support_ticket_status NOT NULL DEFAULT 'OPEN',
            priority        support_ticket_priority NOT NULL DEFAULT 'MEDIUM',
            category        TEXT NOT NULL DEFAULT 'general',
            reporter_email  TEXT NOT NULL,
            reporter_name   TEXT NOT NULL DEFAULT '',
            assignee_email  TEXT,
            resolution_note TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at     TIMESTAMPTZ,
            sla_deadline    TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE INDEX ix_support_tickets_tenant ON support_tickets(tenant_id);
        CREATE INDEX ix_support_tickets_status ON support_tickets(status);
        CREATE INDEX ix_support_tickets_created ON support_tickets(created_at DESC);
    """)
    op.execute("""
        CREATE TABLE support_ticket_comments (
            id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            ticket_id   TEXT NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
            author      TEXT NOT NULL,
            body        TEXT NOT NULL,
            is_internal BOOLEAN NOT NULL DEFAULT FALSE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX ix_ticket_comments_ticket ON support_ticket_comments(ticket_id)")
    op.execute("""
        CREATE TABLE impersonation_sessions (
            id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            operator_id     TEXT NOT NULL,
            operator_email  TEXT NOT NULL,
            tenant_id       TEXT NOT NULL,
            reason          TEXT NOT NULL,
            started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ended_at        TIMESTAMPTZ,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            ip_address      TEXT,
            actions_count   INTEGER NOT NULL DEFAULT 0
        )
    """)
    op.execute("""
        CREATE INDEX ix_impersonation_tenant ON impersonation_sessions(tenant_id);
        CREATE INDEX ix_impersonation_operator ON impersonation_sessions(operator_id);
        CREATE INDEX ix_impersonation_active ON impersonation_sessions(is_active) WHERE is_active = TRUE;
    """)
    op.execute("""
        CREATE TABLE impersonation_actions (
            id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            session_id     TEXT NOT NULL REFERENCES impersonation_sessions(id) ON DELETE CASCADE,
            action_type    TEXT NOT NULL,
            resource       TEXT NOT NULL,
            details        JSONB NOT NULL DEFAULT '{}',
            performed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX ix_impersonation_actions_session ON impersonation_actions(session_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS impersonation_actions")
    op.execute("DROP TABLE IF EXISTS impersonation_sessions")
    op.execute("DROP TABLE IF EXISTS support_ticket_comments")
    op.execute("DROP TABLE IF EXISTS support_tickets")
    op.execute("DROP TYPE IF EXISTS support_ticket_priority")
    op.execute("DROP TYPE IF EXISTS support_ticket_status")
