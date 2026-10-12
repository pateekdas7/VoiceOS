-- LeadSquared CRM integration tables (W4)

CREATE TABLE IF NOT EXISTS leadsquared_credentials (
    cred_id         UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    access_key      TEXT        NOT NULL,
    secret_key      TEXT        NOT NULL,
    api_base_url    TEXT        NOT NULL DEFAULT 'https://api.leadsquared.com',
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ls_creds_tenant UNIQUE (tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_ls_creds_tenant ON leadsquared_credentials (tenant_id) WHERE is_active;

-- Default field mappings (voiceos → leadsquared schema names)
CREATE TABLE IF NOT EXISTS leadsquared_default_field_mapping (
    mapping_id      SERIAL      PRIMARY KEY,
    voiceos_field   TEXT        NOT NULL UNIQUE,
    ls_field        TEXT        NOT NULL
);

INSERT INTO leadsquared_default_field_mapping (voiceos_field, ls_field) VALUES
    (disposition,          mx_LastCallStatus),
    (call_duration_s,      mx_LastCallDuration),
    (call_date,            mx_LastCallDate),
    (call_recording_url,   mx_RecordingUrl),
    (ptp_amount,           mx_PTPAmount),
    (ptp_date,             mx_PTPDate),
    (ptp_status,           mx_PTPStatus),
    (settlement_amount,    mx_SettlementAmount),
    (settlement_expiry,    mx_SettlementExpiry)
ON CONFLICT (voiceos_field) DO NOTHING;

-- Per-tenant field mapping overrides
CREATE TABLE IF NOT EXISTS leadsquared_field_mapping (
    fm_id           UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    voiceos_field   TEXT        NOT NULL,
    ls_field        TEXT        NOT NULL,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ls_field_map UNIQUE (tenant_id, voiceos_field)
);

CREATE INDEX IF NOT EXISTS idx_ls_field_map_tenant ON leadsquared_field_mapping (tenant_id) WHERE is_active;

-- Sync log: tracks every outbound sync attempt per entity
CREATE TABLE IF NOT EXISTS crm_sync_log (
    sync_id         UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    entity_type     TEXT        NOT NULL,   -- disposition | ptp | settlement
    entity_id       UUID        NOT NULL,
    sync_status     TEXT        NOT NULL DEFAULT PENDING,
    ls_lead_id      TEXT,
    attempt_count   INT         NOT NULL DEFAULT 0,
    last_error      TEXT,
    synced_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_crm_sync_entity UNIQUE (tenant_id, entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_crm_sync_status  ON crm_sync_log (sync_status) WHERE sync_status IN (PENDING,FAILED);
CREATE INDEX IF NOT EXISTS idx_crm_sync_tenant  ON crm_sync_log (tenant_id, updated_at DESC);

-- Import log: one row per inbound pull-from-LS job
CREATE TABLE IF NOT EXISTS leadsquared_import_log (
    import_id       UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id       UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    campaign_id     UUID        REFERENCES campaigns (campaign_id) ON DELETE SET NULL,
    status          TEXT        NOT NULL DEFAULT RUNNING,
    filters         JSONB       NOT NULL DEFAULT '[]'::jsonb,
    leads_fetched   INT         NOT NULL DEFAULT 0,
    leads_created   INT         NOT NULL DEFAULT 0,
    leads_updated   INT         NOT NULL DEFAULT 0,
    leads_skipped   INT         NOT NULL DEFAULT 0,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ls_import_tenant ON leadsquared_import_log (tenant_id, created_at DESC);
