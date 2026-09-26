-- Migration 037: tenant-scoped production telephony numbers.
-- Provider credentials are deliberately not stored here.
BEGIN;
CREATE TABLE IF NOT EXISTS telephony_phone_numbers (
    phone_number_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    campaign_id UUID REFERENCES campaigns(campaign_id) ON DELETE SET NULL,
    provider TEXT NOT NULL CHECK (provider IN ('twilio','sip')),
    provider_number_id TEXT NOT NULL,
    e164_number TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','SUSPENDED','RELEASED')),
    inbound_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    outbound_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, provider_number_id),
    UNIQUE (provider, e164_number)
);
CREATE INDEX IF NOT EXISTS idx_telephony_numbers_tenant ON telephony_phone_numbers (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_telephony_numbers_campaign ON telephony_phone_numbers (tenant_id, campaign_id, status);
CREATE INDEX IF NOT EXISTS idx_telephony_numbers_e164 ON telephony_phone_numbers (e164_number, status);
GRANT ALL PRIVILEGES ON TABLE telephony_phone_numbers TO voiceos;
COMMIT;
