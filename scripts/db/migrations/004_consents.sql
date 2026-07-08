-- Migration 004: Consent Management (DPDP / GDPR)
-- Architecture: V4 Ch2 (DPDP §7–§8); V4 Ch5 (Privacy).
-- Consent records are append-only. The consents table holds current state;
-- consent_records holds the immutable audit history.

CREATE TABLE IF NOT EXISTS consents (
    consent_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL,
    customer_id             UUID        NOT NULL REFERENCES customers (customer_id) ON DELETE CASCADE,
    consent_type            TEXT        NOT NULL,
    status                  TEXT        NOT NULL,       -- ConsentStatus: 'GRANTED' | 'REVOKED' | 'PENDING'
    granted_at              TIMESTAMPTZ,
    revoked_at              TIMESTAMPTZ,
    expires_at              TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL,
    updated_at              TIMESTAMPTZ NOT NULL,

    CONSTRAINT uq_consents_customer_type UNIQUE (customer_id, consent_type)
);

CREATE INDEX IF NOT EXISTS idx_consents_tenant_id      ON consents (tenant_id);
CREATE INDEX IF NOT EXISTS idx_consents_customer_id    ON consents (customer_id);
CREATE INDEX IF NOT EXISTS idx_consents_status         ON consents (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_consents_type_status    ON consents (customer_id, consent_type, status);

-- Consent audit trail (immutable — no UPDATE/DELETE)
CREATE TABLE IF NOT EXISTS consent_records (
    record_id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    consent_id              UUID        NOT NULL REFERENCES consents (consent_id),
    action                  TEXT        NOT NULL,       -- 'GRANTED' | 'REVOKED' | 'RENEWED' | 'EXPIRED'
    actor_id                TEXT        NOT NULL,
    channel                 TEXT        NOT NULL,
    recorded_at             TIMESTAMPTZ NOT NULL,
    ip_address              TEXT        NOT NULL DEFAULT '',
    notes                   TEXT        NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_consent_records_consent_id ON consent_records (consent_id);
CREATE INDEX IF NOT EXISTS idx_consent_records_recorded_at ON consent_records (recorded_at DESC);
