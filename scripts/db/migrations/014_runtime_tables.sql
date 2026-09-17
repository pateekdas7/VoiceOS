-- Migration 014: Runtime tables referenced by bff.js + dialer_worker.js
-- Adds lead_enrichment_log, active_calls, worker_heartbeats, and
-- campaigns.enrichment_config column expected by BFF at runtime.

BEGIN;

-- ── 1. Campaign enrichment config column ─────────────────────────────────────
ALTER TABLE campaigns
    ADD COLUMN IF NOT EXISTS enrichment_config JSONB NOT NULL DEFAULT '{}';

-- ── 2. Lead enrichment log ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lead_enrichment_log (
    log_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id     UUID        REFERENCES leads(lead_id) ON DELETE SET NULL,
    lead_phone  TEXT        NOT NULL,
    provider    TEXT        NOT NULL,
    result      JSONB       NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_enrich_log_phone      ON lead_enrichment_log(lead_phone);
CREATE INDEX IF NOT EXISTS idx_enrich_log_lead       ON lead_enrichment_log(lead_id);
CREATE INDEX IF NOT EXISTS idx_enrich_log_created    ON lead_enrichment_log(created_at DESC);

-- ── 3. Active calls (call state during a live Twilio session) ────────────────
CREATE TABLE IF NOT EXISTS active_calls (
    call_sid       TEXT        PRIMARY KEY,
    lead_id        UUID        REFERENCES leads(lead_id) ON DELETE SET NULL,
    campaign_id    UUID        NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    pipeline_id    UUID        REFERENCES pipelines(pipeline_id) ON DELETE SET NULL,
    tenant_id      UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    phone          TEXT        NOT NULL,
    lead_name      TEXT        NOT NULL DEFAULT '',
    language       TEXT        NOT NULL DEFAULT 'HINDI',
    status         TEXT        NOT NULL DEFAULT 'INITIATING'
                               CHECK (status IN ('INITIATING','RINGING','IN_PROGRESS','COMPLETED','FAILED','NO_ANSWER','BUSY','CANCELLED')),
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at       TIMESTAMPTZ,
    duration_sec   INTEGER,
    outcome        TEXT,
    metadata       JSONB       NOT NULL DEFAULT '{}',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_active_calls_tenant   ON active_calls(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_active_calls_lead     ON active_calls(lead_id);
CREATE INDEX IF NOT EXISTS idx_active_calls_campaign ON active_calls(campaign_id, started_at DESC);

-- ── 4. Worker heartbeats (dialer_worker liveness) ────────────────────────────
CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_id         TEXT        PRIMARY KEY,
    status            TEXT        NOT NULL DEFAULT 'RUNNING'
                                  CHECK (status IN ('RUNNING','DRAINING','STOPPED','ERROR')),
    active_calls      INTEGER     NOT NULL DEFAULT 0,
    idle_pipelines    INTEGER     NOT NULL DEFAULT 0,
    busy_pipelines    INTEGER     NOT NULL DEFAULT 0,
    queues_monitored  TEXT[]      NOT NULL DEFAULT '{}',
    calls_today       INTEGER     NOT NULL DEFAULT 0,
    calls_per_minute  NUMERIC     NOT NULL DEFAULT 0,
    last_heartbeat    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata          JSONB       NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_worker_hb_status ON worker_heartbeats(status, last_heartbeat DESC);

COMMIT;
