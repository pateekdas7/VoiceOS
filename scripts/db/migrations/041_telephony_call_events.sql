-- Migration 041: durable canonical telephony call events.
BEGIN;
CREATE TABLE IF NOT EXISTS telephony_call_events (
    event_id TEXT PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    campaign_id UUID,
    lead_id UUID,
    call_id TEXT NOT NULL,
    call_attempt_id UUID REFERENCES call_attempts(attempt_id) ON DELETE SET NULL,
    provider_call_sid TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    outcome TEXT,
    duration_seconds INTEGER,
    recording_reference TEXT,
    callback_reference TEXT,
    event_timestamp TIMESTAMPTZ NOT NULL,
    correlation_id TEXT,
    sequence_no INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, call_attempt_id, lifecycle_state)
);
CREATE INDEX IF NOT EXISTS idx_telephony_call_events_tenant_time ON telephony_call_events (tenant_id, event_timestamp);
CREATE INDEX IF NOT EXISTS idx_telephony_call_events_call ON telephony_call_events (tenant_id, provider_call_sid, sequence_no);
GRANT ALL PRIVILEGES ON TABLE telephony_call_events TO voiceos;
COMMIT;