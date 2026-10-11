-- Migration 016: call_attempts — durable pre-call record for every outbound attempt.
--
-- Purpose:
--   Before this migration, the dialer wrote to active_calls AFTER the Twilio call was
--   initiated, in a fire-and-forget setImmediate. A crash between Twilio initiation and
--   the write left no trace of the attempt, making crash reconciliation impossible.
--
--   call_attempts is written BEFORE the Twilio API call with status=INITIATED.
--   The call_sid is back-filled once Twilio responds. Phase 3 (crash reconciliation)
--   uses this table to recover stuck leads on worker restart.
--
-- Rollback: DROP TABLE IF EXISTS call_attempts;
--           (safe — no other table references it as FK source)

BEGIN;

CREATE TABLE IF NOT EXISTS call_attempts (
    attempt_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    campaign_id     UUID        NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    lead_id         UUID        NOT NULL,
    pipeline_id     UUID,
    call_sid        TEXT        UNIQUE,
    worker_id       TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'INITIATED'
                    CHECK (status IN ('INITIATED','IN_PROGRESS','COMPLETED',
                                      'FAILED','TIMEOUT','NO_ANSWER','BUSY')),
    disposition     TEXT,
    duration_s      INTEGER     CHECK (duration_s IS NULL OR duration_s >= 0),
    initiated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    answered_at     TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    error_message   TEXT
);

-- Phase 3 (reconciliation) scans for IN_PROGRESS attempts older than MAX_CALL_DURATION
CREATE INDEX IF NOT EXISTS idx_call_attempts_open
    ON call_attempts (worker_id, status, initiated_at)
    WHERE status IN ('INITIATED', 'IN_PROGRESS');

-- Analytics and per-lead lookup
CREATE INDEX IF NOT EXISTS idx_call_attempts_lead
    ON call_attempts (lead_id, initiated_at DESC);

CREATE INDEX IF NOT EXISTS idx_call_attempts_tenant
    ON call_attempts (tenant_id, initiated_at DESC);

-- call_sid lookup for callback idempotency cross-check
CREATE INDEX IF NOT EXISTS idx_call_attempts_sid
    ON call_attempts (call_sid)
    WHERE call_sid IS NOT NULL;

GRANT ALL PRIVILEGES ON TABLE call_attempts TO voiceos;

COMMIT;
