-- Migration 001: Customers and Parties
-- Architecture: V5 Ch3 (Customer CRM); V4 Ch5 (Privacy / PII)
-- All PII fields are nullable to support DPDP §13 data erasure (set to NULL on erasure).

CREATE TABLE IF NOT EXISTS customers (
    customer_id         UUID        PRIMARY KEY,
    tenant_id           UUID        NOT NULL,
    crm_id              TEXT        NOT NULL,           -- external lending system ID (authoritative, RI-5)
    name                TEXT,                           -- nullable: PII — erased on DataErasureRequested
    preferred_language  TEXT        NOT NULL DEFAULT 'en',
    is_active           BOOLEAN     NOT NULL DEFAULT TRUE,
    data_erasure_requested BOOLEAN  NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL,

    CONSTRAINT uq_customers_tenant_crm UNIQUE (tenant_id, crm_id)
);

CREATE INDEX IF NOT EXISTS idx_customers_tenant_id     ON customers (tenant_id);
CREATE INDEX IF NOT EXISTS idx_customers_crm_id        ON customers (crm_id);
CREATE INDEX IF NOT EXISTS idx_customers_is_active     ON customers (tenant_id, is_active);

-- Customer contacts (phone numbers, email addresses)
CREATE TABLE IF NOT EXISTS customer_contacts (
    contact_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id         UUID        NOT NULL REFERENCES customers (customer_id) ON DELETE CASCADE,
    tenant_id           UUID        NOT NULL,
    contact_type        TEXT        NOT NULL,           -- 'MOBILE' | 'HOME' | 'OFFICE' | 'EMAIL' | 'WHATSAPP'
    value               TEXT,                           -- nullable: PII — erased on DataErasureRequested
    is_primary          BOOLEAN     NOT NULL DEFAULT FALSE,
    is_dnc              BOOLEAN     NOT NULL DEFAULT FALSE,
    consent_captured    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contacts_customer_id    ON customer_contacts (customer_id);
CREATE INDEX IF NOT EXISTS idx_contacts_tenant_id      ON customer_contacts (tenant_id);
CREATE INDEX IF NOT EXISTS idx_contacts_is_dnc         ON customer_contacts (tenant_id, is_dnc);

-- Customer addresses
CREATE TABLE IF NOT EXISTS customer_addresses (
    address_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id         UUID        NOT NULL REFERENCES customers (customer_id) ON DELETE CASCADE,
    tenant_id           UUID        NOT NULL,
    line1               TEXT,                           -- nullable: PII
    line2               TEXT,
    city                TEXT,
    state               TEXT,
    pincode             TEXT,
    country             CHAR(2)     NOT NULL DEFAULT 'IN',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_addresses_customer_id   ON customer_addresses (customer_id);

-- Loan parties (co-borrowers, guarantors)
CREATE TABLE IF NOT EXISTS parties (
    party_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id         UUID        NOT NULL REFERENCES customers (customer_id) ON DELETE CASCADE,
    tenant_id           UUID        NOT NULL,
    role                TEXT        NOT NULL,           -- PartyRole enum value
    name                TEXT,                           -- nullable: PII
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_parties_customer_id     ON parties (customer_id);
CREATE INDEX IF NOT EXISTS idx_parties_tenant_id       ON parties (tenant_id);
