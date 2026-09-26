"""telephony recording lifecycle metadata

Revision ID: 0039
Revises: 0038
"""
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""-- Migration 039: durable tenant-scoped recording lifecycle metadata.
BEGIN;
CREATE TABLE IF NOT EXISTS telephony_recordings (
    recording_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    campaign_id UUID REFERENCES campaigns(campaign_id) ON DELETE SET NULL,
    lead_id UUID REFERENCES leads(lead_id) ON DELETE SET NULL,
    call_attempt_id UUID REFERENCES call_attempts(attempt_id) ON DELETE SET NULL,
    call_sid TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'twilio',
    provider_recording_id TEXT,
    state TEXT NOT NULL DEFAULT 'CREATED'
      CHECK (state IN ('CREATED','PROCESSING','AVAILABLE','RETAINED','FAILED','DELETED')),
    object_key TEXT,
    content_type TEXT,
    byte_size BIGINT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    retention_until TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    delete_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, call_sid),
    UNIQUE (provider, provider_recording_id)
);
CREATE INDEX IF NOT EXISTS idx_telephony_recordings_tenant_state ON telephony_recordings (tenant_id, state, retention_until);
CREATE INDEX IF NOT EXISTS idx_telephony_recordings_call_attempt ON telephony_recordings (call_attempt_id);
CREATE INDEX IF NOT EXISTS idx_telephony_recordings_call_sid ON telephony_recordings (call_sid);
CREATE TABLE IF NOT EXISTS telephony_recording_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    recording_id UUID REFERENCES telephony_recordings(recording_id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, provider_event_id)
);
CREATE INDEX IF NOT EXISTS idx_telephony_recording_events_recording ON telephony_recording_events (recording_id, created_at);
GRANT ALL PRIVILEGES ON TABLE telephony_recordings TO voiceos;
GRANT ALL PRIVILEGES ON TABLE telephony_recording_events TO voiceos;
COMMIT;""")

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS telephony_recording_events")
    op.execute("DROP TABLE IF EXISTS telephony_recordings")
