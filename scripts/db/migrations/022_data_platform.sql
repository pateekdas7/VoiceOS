-- W14: Data Platform — raw event store, retention, reporting aggregates

-- ── Raw event store (append-only, partitioned by month) ───────────────────────
CREATE TABLE IF NOT EXISTS raw_events (
    event_id        TEXT        NOT NULL,
    event_type      TEXT        NOT NULL,
    schema_version  INT         NOT NULL DEFAULT 1,
    tenant_id       UUID        NOT NULL,
    correlation_id  TEXT        NOT NULL,
    causation_id    TEXT,
    trace_id        TEXT        NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT pk_raw_events PRIMARY KEY (event_id, occurred_at)
) PARTITION BY RANGE (occurred_at);

-- Create initial monthly partitions (current month + 2 forward)
CREATE TABLE IF NOT EXISTS raw_events_2026_10 PARTITION OF raw_events
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');

CREATE TABLE IF NOT EXISTS raw_events_2026_11 PARTITION OF raw_events
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');

CREATE TABLE IF NOT EXISTS raw_events_2026_12 PARTITION OF raw_events
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

CREATE INDEX IF NOT EXISTS idx_raw_events_tenant    ON raw_events (tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_events_type      ON raw_events (event_type, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_events_corr      ON raw_events (correlation_id);

-- ── Event retention policy table ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS event_retention_policies (
    policy_id       SERIAL      PRIMARY KEY,
    event_type_glob TEXT        NOT NULL,   -- glob pattern e.g. call.*, *
    retain_days     INT         NOT NULL DEFAULT 365,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_retention_glob UNIQUE (event_type_glob)
);

INSERT INTO event_retention_policies (event_type_glob, retain_days) VALUES
    (saas.billing.*,  2555),   -- 7 years (regulatory)
    (call.*,          365),
    (ptp.*,           730),
    (consent.*,       2555),
    (audit.*,         2555),
    (*,               90)
ON CONFLICT (event_type_glob) DO NOTHING;

-- ── Reporting aggregates: daily call summary per tenant/campaign ───────────────
CREATE TABLE IF NOT EXISTS rpt_daily_call_summary (
    summary_date    DATE        NOT NULL,
    tenant_id       UUID        NOT NULL,
    campaign_id     UUID,
    calls_attempted INT         NOT NULL DEFAULT 0,
    calls_connected INT         NOT NULL DEFAULT 0,
    calls_answered  INT         NOT NULL DEFAULT 0,
    ptps_captured   INT         NOT NULL DEFAULT 0,
    total_duration_s INT        NOT NULL DEFAULT 0,
    refreshed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_rpt_daily_call PRIMARY KEY (summary_date, tenant_id, COALESCE(campaign_id, '00000000-0000-0000-0000-000000000000'::uuid))
);

CREATE INDEX IF NOT EXISTS idx_rpt_daily_tenant ON rpt_daily_call_summary (tenant_id, summary_date DESC);

-- ── ETL consumer checkpoint (Redis Streams last-seen ID per consumer group) ───
CREATE TABLE IF NOT EXISTS event_consumer_checkpoint (
    consumer_group  TEXT        NOT NULL PRIMARY KEY,
    last_entry_id   TEXT        NOT NULL DEFAULT '0-0',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO event_consumer_checkpoint (consumer_group, last_entry_id)
VALUES ('data-platform-etl', '0-0')
ON CONFLICT (consumer_group) DO NOTHING;
