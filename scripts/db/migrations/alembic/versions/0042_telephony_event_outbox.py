"""durable canonical telephony event outbox and DLQ

Revision ID: 0042
Revises: 0041
"""
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE telephony_call_events
      ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'twilio'""")
    op.execute("""CREATE TABLE IF NOT EXISTS telephony_event_outbox (
        event_id TEXT PRIMARY KEY REFERENCES telephony_call_events(event_id) ON DELETE CASCADE,
        tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
        event_type TEXT NOT NULL,
        schema_version TEXT NOT NULL,
        payload JSONB NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('PENDING','PROCESSING','PUBLISHED','DLQ')),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        locked_at TIMESTAMPTZ,
        published_at TIMESTAMPTZ,
        last_error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")
    op.execute("""CREATE INDEX IF NOT EXISTS idx_telephony_event_outbox_ready
      ON telephony_event_outbox (status, next_attempt_at)
      WHERE status IN ('PENDING','PROCESSING')""")
    op.execute("""CREATE INDEX IF NOT EXISTS idx_telephony_event_outbox_tenant
      ON telephony_event_outbox (tenant_id, created_at)""")
    op.execute("""CREATE TABLE IF NOT EXISTS telephony_event_dlq (
        event_id TEXT PRIMARY KEY REFERENCES telephony_call_events(event_id) ON DELETE RESTRICT,
        tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
        event_type TEXT NOT NULL,
        schema_version TEXT NOT NULL,
        payload JSONB NOT NULL,
        attempts INTEGER NOT NULL CHECK (attempts > 0),
        failure_code TEXT NOT NULL,
        last_error TEXT,
        failed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")
    op.execute("""CREATE INDEX IF NOT EXISTS idx_telephony_event_dlq_tenant
      ON telephony_event_dlq (tenant_id, failed_at)""")
    op.execute("GRANT ALL PRIVILEGES ON TABLE telephony_event_outbox TO voiceos")
    op.execute("GRANT ALL PRIVILEGES ON TABLE telephony_event_dlq TO voiceos")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS telephony_event_dlq")
    op.execute("DROP TABLE IF EXISTS telephony_event_outbox")
    op.execute("ALTER TABLE telephony_call_events DROP COLUMN IF EXISTS provider")
