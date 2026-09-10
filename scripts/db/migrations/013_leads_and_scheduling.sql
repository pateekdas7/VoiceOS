-- Migration 013: Leads pipeline + campaign scheduling extensions
-- Fixes:
--   - Creates BFF-referenced tables that migration 012 never created
--     matching its actual runtime shape (leads, lead_imports, pipelines,
--     lead_execution_events, pipeline_distribution_rules,
--     campaign_qualification_rules).
--   - Adds weekday/excluded-date scheduling to campaigns.
--   - Adds NOW() defaults on campaigns.created_at/updated_at (was causing
--     BFF POST /campaigns to 500 with NOT NULL violation).

BEGIN;

-- ── 1. Campaign scheduling extensions ────────────────────────────────────────
ALTER TABLE campaigns
    ALTER COLUMN created_at SET DEFAULT NOW(),
    ALTER COLUMN updated_at SET DEFAULT NOW();

ALTER TABLE campaigns
    ADD COLUMN IF NOT EXISTS allowed_weekdays SMALLINT[] NOT NULL DEFAULT '{1,2,3,4,5,6,7}',
    ADD COLUMN IF NOT EXISTS excluded_dates    DATE[]    NOT NULL DEFAULT '{}';

-- Enforce weekday values in the ISO range 1..7 (1=Mon, 7=Sun).
ALTER TABLE campaigns
    DROP CONSTRAINT IF EXISTS ck_campaigns_allowed_weekdays;
ALTER TABLE campaigns
    ADD CONSTRAINT ck_campaigns_allowed_weekdays
    CHECK (allowed_weekdays <@ ARRAY[1,2,3,4,5,6,7]::SMALLINT[]);

-- ── 2. Pipelines ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipelines (
    pipeline_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    campaign_id     UUID        NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    name            TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'ACTIVE'
                                CHECK (status IN ('DRAFT','ACTIVE','PAUSED','ARCHIVED')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      TEXT        NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pipelines_tenant_id   ON pipelines(tenant_id);
CREATE INDEX IF NOT EXISTS idx_pipelines_campaign_id ON pipelines(tenant_id, campaign_id);

-- ── 3. Lead imports (BFF-runtime shape) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS lead_imports (
    import_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    campaign_id         UUID        NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    filename            TEXT        NOT NULL,
    original_columns    JSONB       NOT NULL DEFAULT '[]',
    column_mapping      JSONB       NOT NULL DEFAULT '{}',
    rows_data           JSONB       NOT NULL DEFAULT '[]',
    status              TEXT        NOT NULL DEFAULT 'PROCESSING'
                                    CHECK (status IN ('PROCESSING','DONE','FAILED')),
    total_rows          INTEGER     NOT NULL DEFAULT 0 CHECK (total_rows          >= 0),
    valid_rows          INTEGER     NOT NULL DEFAULT 0 CHECK (valid_rows          >= 0),
    invalid_rows        INTEGER     NOT NULL DEFAULT 0 CHECK (invalid_rows        >= 0),
    duplicate_rows      INTEGER     NOT NULL DEFAULT 0 CHECK (duplicate_rows      >= 0),
    last_processed_row  INTEGER     NOT NULL DEFAULT 0 CHECK (last_processed_row  >= 0),
    failed_rows         JSONB       NOT NULL DEFAULT '[]',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_lead_imports_campaign_id ON lead_imports(tenant_id, campaign_id);

-- ── 4. Leads (matches bff.js:786 INSERT column order) ────────────────────────
CREATE TABLE IF NOT EXISTS leads (
    lead_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id         UUID        NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    pipeline_id         UUID        REFERENCES pipelines(pipeline_id) ON DELETE SET NULL,
    tenant_id           UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    import_id           UUID        REFERENCES lead_imports(import_id) ON DELETE SET NULL,
    name                TEXT        NOT NULL DEFAULT '',
    phone               TEXT        NOT NULL,
    email               TEXT,
    language            TEXT        NOT NULL DEFAULT 'HINDI',
    score               SMALLINT    NOT NULL DEFAULT 0 CHECK (score BETWEEN 0 AND 100),
    qualified           BOOLEAN     NOT NULL DEFAULT FALSE,
    status              TEXT        NOT NULL DEFAULT 'PENDING'
                                    CHECK (status IN ('PENDING','VALIDATED','QUALIFIED','ASSIGNED','CALLED','COMPLETED','REJECTED')),
    queue_status        TEXT        NOT NULL DEFAULT 'PENDING'
                                    CHECK (queue_status IN ('PENDING','QUEUED','IN_CALL','DONE','FAILED')),
    rejection_reason    TEXT,
    metadata            JSONB       NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (campaign_id, phone)
);
CREATE INDEX IF NOT EXISTS idx_leads_tenant_id   ON leads(tenant_id);
CREATE INDEX IF NOT EXISTS idx_leads_campaign_id ON leads(tenant_id, campaign_id);
CREATE INDEX IF NOT EXISTS idx_leads_pipeline_id ON leads(pipeline_id);
CREATE INDEX IF NOT EXISTS idx_leads_queue       ON leads(tenant_id, queue_status, status);

-- ── 5. Lead execution events (audit trail) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS lead_execution_events (
    event_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id        UUID        REFERENCES leads(lead_id) ON DELETE CASCADE,
    campaign_id    UUID        NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    pipeline_id    UUID        REFERENCES pipelines(pipeline_id) ON DELETE SET NULL,
    tenant_id      UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    event_type     TEXT        NOT NULL,
    status         TEXT        NOT NULL DEFAULT 'SUCCESS'
                               CHECK (status IN ('SUCCESS','FAILURE','WARN','INFO')),
    message        TEXT        NOT NULL DEFAULT '',
    metadata       JSONB       NOT NULL DEFAULT '{}',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lead_events_lead_id    ON lead_execution_events(lead_id);
CREATE INDEX IF NOT EXISTS idx_lead_events_campaign   ON lead_execution_events(tenant_id, campaign_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_events_pipeline   ON lead_execution_events(pipeline_id, created_at DESC);

-- ── 6. Pipeline distribution rules ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline_distribution_rules (
    rule_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    campaign_id   UUID        NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    pipeline_id   UUID        NOT NULL REFERENCES pipelines(pipeline_id) ON DELETE CASCADE,
    min_score     SMALLINT    NOT NULL DEFAULT 0   CHECK (min_score BETWEEN 0 AND 100),
    max_score     SMALLINT    NOT NULL DEFAULT 100 CHECK (max_score BETWEEN 0 AND 100),
    languages     TEXT[]      NOT NULL DEFAULT '{}',
    priority      SMALLINT    NOT NULL DEFAULT 0,
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (max_score >= min_score)
);
CREATE INDEX IF NOT EXISTS idx_pdr_campaign  ON pipeline_distribution_rules(tenant_id, campaign_id, is_active);

-- ── 7. Campaign qualification rules ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaign_qualification_rules (
    rule_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    campaign_id   UUID        NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    field         TEXT        NOT NULL,
    operator      TEXT        NOT NULL,
    value         TEXT        NOT NULL DEFAULT '',
    action        TEXT        NOT NULL DEFAULT 'REQUIRE'
                              CHECK (action IN ('REQUIRE','REJECT')),
    priority      SMALLINT    NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cqr_campaign ON campaign_qualification_rules(tenant_id, campaign_id);

COMMIT;
