-- Migration 015: Extend pipelines table with dialer_worker runtime columns
-- Adds BUSY/IDLE statuses + call bookkeeping columns.

BEGIN;

-- Widen status set to match dialer_worker (BUSY/IDLE runtime + config states)
ALTER TABLE pipelines DROP CONSTRAINT IF EXISTS pipelines_status_check;
ALTER TABLE pipelines
    ADD CONSTRAINT pipelines_status_check
    CHECK (status IN ('DRAFT','ACTIVE','PAUSED','ARCHIVED','IDLE','BUSY'));

-- Runtime call-tracking columns
ALTER TABLE pipelines
    ADD COLUMN IF NOT EXISTS current_lead_id     UUID     REFERENCES leads(lead_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS current_call_sid    TEXT,
    ADD COLUMN IF NOT EXISTS calls_completed     INTEGER  NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS calls_no_answer     INTEGER  NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS calls_failed        INTEGER  NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_duration_s    INTEGER  NOT NULL DEFAULT 0;

GRANT ALL PRIVILEGES ON TABLE pipelines TO voiceos;

COMMIT;
