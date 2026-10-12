-- Migration 019: DNC list + per-tenant telephony config

-- Per-tenant provider phone numbers (caller ID management)
CREATE TABLE IF NOT EXISTS tenant_telephony_config (
    config_id    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    provider     TEXT        NOT NULL CHECK (provider IN ('twilio','exotel','plivo','telnyx')),
    from_number  TEXT        NOT NULL,
    display_name TEXT,
    is_active    BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, provider, from_number)
);
CREATE INDEX IF NOT EXISTS idx_ttc_tenant_provider ON tenant_telephony_config (tenant_id, provider) WHERE is_active;

COMMENT ON TABLE tenant_telephony_config IS 'Per-tenant caller ID per provider. Used by MultiProviderDialer to pick the from_number for each tenant.';

-- DNC (Do Not Call) registry — global + per-tenant blocked numbers
CREATE TABLE IF NOT EXISTS dnc_numbers (
    dnc_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    phone      TEXT        NOT NULL,
    tenant_id  UUID        REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    source     TEXT        NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','ndnc','trai','tenant_upload','opted_out')),
    reason     TEXT,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    UNIQUE (phone, tenant_id)
);
-- NULL tenant_id = global (applies to all tenants)
CREATE INDEX IF NOT EXISTS idx_dnc_phone        ON dnc_numbers (phone);
CREATE INDEX IF NOT EXISTS idx_dnc_tenant_phone ON dnc_numbers (tenant_id, phone) WHERE tenant_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_dnc_global       ON dnc_numbers (phone) WHERE tenant_id IS NULL;

COMMENT ON TABLE dnc_numbers IS 'DNC registry. tenant_id NULL = global block. Checked by dialer_worker before every call.';
