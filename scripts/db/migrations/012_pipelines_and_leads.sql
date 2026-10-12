-- Migration 012: Pipelines and Campaign Leads (Lead Distribution Engine)
-- Architecture: V5 Ch6 (Campaign Engine — Lead Distribution, ADR-005 §4.3/§14).

-- ────────────────────────────────────────────────────────────────────────────
-- Pipelines — agent/team work queues scoped to one campaign.
-- Each pipeline owns a slice of the campaign's imported lead pool.
-- Pipelines have their own lifecycle (DRAFT → ACTIVE → PAUSED → ARCHIVED)
-- and are independent of the parent campaign's status.
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pipelines (
    pipeline_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    campaign_id     UUID        NOT NULL REFERENCES campaigns (campaign_id) ON DELETE CASCADE,
    name            TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'DRAFT'
                                CHECK (status IN ('DRAFT', 'ACTIVE', 'PAUSED', 'ARCHIVED')),
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    created_by      TEXT        NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pipelines_tenant_id    ON pipelines (tenant_id);
CREATE INDEX IF NOT EXISTS idx_pipelines_campaign_id  ON pipelines (tenant_id, campaign_id);
CREATE INDEX IF NOT EXISTS idx_pipelines_status       ON pipelines (tenant_id, status);

-- ────────────────────────────────────────────────────────────────────────────
-- Lead imports — tracks each CSV bulk-upload operation.
-- Created at upload time; updated to DONE/FAILED when processing completes.
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS lead_imports (
    import_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    campaign_id     UUID        NOT NULL REFERENCES campaigns (campaign_id) ON DELETE CASCADE,
    filename        TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'PROCESSING'
                                CHECK (status IN ('PROCESSING', 'DONE', 'FAILED')),
    total_rows      INT         NOT NULL DEFAULT 0 CHECK (total_rows >= 0),
    valid_rows      INT         NOT NULL DEFAULT 0 CHECK (valid_rows >= 0),
    invalid_rows    INT         NOT NULL DEFAULT 0 CHECK (invalid_rows >= 0),
    duplicate_rows  INT         NOT NULL DEFAULT 0 CHECK (duplicate_rows >= 0),
    created_at      TIMESTAMPTZ NOT NULL,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_lead_imports_tenant_id   ON lead_imports (tenant_id);
CREATE INDEX IF NOT EXISTS idx_lead_imports_campaign_id ON lead_imports (tenant_id, campaign_id);

-- ────────────────────────────────────────────────────────────────────────────
-- Campaign leads — individual contacts imported into a campaign.
-- Sourced from client CSV uploads; distinct from the AudienceSelector cohort
-- (campaign_audiences), which is built from CRM/loan-book criteria.
-- A campaign_lead is assigned to exactly one pipeline once the Lead
-- Distribution Engine processes it; pipeline_id NULL means unassigned.
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS campaign_leads (
    lead_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    campaign_id         UUID        NOT NULL REFERENCES campaigns (campaign_id) ON DELETE CASCADE,
    pipeline_id         UUID        REFERENCES pipelines (pipeline_id) ON DELETE SET NULL,
    import_id           UUID        REFERENCES lead_imports (import_id) ON DELETE SET NULL,
    -- Contact
    phone               TEXT        NOT NULL,   -- normalized E.164 (+91XXXXXXXXXX)
    phone_raw           TEXT        NOT NULL,   -- original as uploaded
    name                TEXT        NOT NULL DEFAULT '',
    email               TEXT,
    -- Classification
    language            TEXT        NOT NULL DEFAULT 'HINDI'
                                    CHECK (language IN (
                                        'HINDI','TAMIL','TELUGU','MARATHI','GUJARATI',
                                        'KANNADA','MALAYALAM','BENGALI','PUNJABI','ODIA','ENGLISH'
                                    )),
    score               SMALLINT    NOT NULL DEFAULT 0 CHECK (score BETWEEN 0 AND 100),
    -- Status
    status              TEXT        NOT NULL DEFAULT 'NEW'
                                    CHECK (status IN (
                                        'NEW','VALIDATED','QUALIFIED','ASSIGNED',
                                        'CALLED','COMPLETED','REJECTED'
                                    )),
    queue_status        TEXT        NOT NULL DEFAULT 'PENDING'
                                    CHECK (queue_status IN ('PENDING','QUEUED','IN_CALL','DONE')),
    -- Quality gates
    is_duplicate        BOOLEAN     NOT NULL DEFAULT FALSE,
    is_blacklisted      BOOLEAN     NOT NULL DEFAULT FALSE,
    rejection_reason    TEXT,
    -- Extensible attributes (dpd, outstanding, product_type, city, state, etc.)
    metadata            JSONB       NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_leads_campaign_phone
    ON campaign_leads (campaign_id, phone)
    WHERE is_duplicate = FALSE;

CREATE INDEX IF NOT EXISTS idx_leads_tenant_id    ON campaign_leads (tenant_id);
CREATE INDEX IF NOT EXISTS idx_leads_campaign_id  ON campaign_leads (tenant_id, campaign_id);
CREATE INDEX IF NOT EXISTS idx_leads_pipeline_id  ON campaign_leads (pipeline_id);
CREATE INDEX IF NOT EXISTS idx_leads_status       ON campaign_leads (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_leads_score        ON campaign_leads (campaign_id, score DESC);
