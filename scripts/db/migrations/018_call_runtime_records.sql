-- Migration 018: call_runtime_records
-- Runtime call log used by dialer_worker.js (Node.js) to store per-call outcomes.
-- Separate from call_dispositions (Python collections flow) to avoid schema conflict.
-- Added: recording columns for Twilio recording status callback.

CREATE TABLE IF NOT EXISTS call_runtime_records (
    record_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    call_sid           TEXT        NOT NULL UNIQUE,
    lead_id            UUID        NOT NULL,
    campaign_id        UUID        NOT NULL,
    tenant_id          UUID        NOT NULL,
    disposition        TEXT        NOT NULL,
    duration_seconds   INT         NOT NULL DEFAULT 0,
    started_at         TIMESTAMPTZ,
    ended_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    recording_sid      TEXT,
    recording_url      TEXT,
    recording_duration_s INT,
    metadata           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crr_tenant_id   ON call_runtime_records (tenant_id);
CREATE INDEX IF NOT EXISTS idx_crr_lead_id     ON call_runtime_records (lead_id);
CREATE INDEX IF NOT EXISTS idx_crr_campaign_id ON call_runtime_records (campaign_id);
CREATE INDEX IF NOT EXISTS idx_crr_disposition ON call_runtime_records (tenant_id, disposition);
CREATE INDEX IF NOT EXISTS idx_crr_ended_at    ON call_runtime_records (tenant_id, ended_at DESC);

COMMENT ON TABLE call_runtime_records IS 'Per-call runtime records written by dialer_worker (Node.js). Recording URL populated by Twilio recording-status callback.';
