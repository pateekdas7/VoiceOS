-- Migration 010: Billing Subscriptions, Usage Events, and Invoices
-- Architecture: V5 Ch7 (Usage Metering); V5 Ch8 (Billing & Invoicing).
-- All monetary amounts stored in minor currency units.

CREATE TABLE IF NOT EXISTS billing_subscriptions (
    subscription_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    tier                    TEXT        NOT NULL,
    rate_card_version       TEXT        NOT NULL,
    contract_start          TIMESTAMPTZ NOT NULL,
    contract_end            TIMESTAMPTZ,
    base_fee_minor          BIGINT      NOT NULL DEFAULT 0 CHECK (base_fee_minor >= 0),
    currency                CHAR(3)     NOT NULL DEFAULT 'INR',
    is_active               BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL,
    updated_at              TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sub_tenant_id           ON billing_subscriptions (tenant_id);
CREATE INDEX IF NOT EXISTS idx_sub_is_active           ON billing_subscriptions (tenant_id, is_active);

-- Usage events (pre-aggregated into hourly buckets for high-volume dimensions)
CREATE TABLE IF NOT EXISTS usage_events (
    usage_event_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    usage_type              TEXT        NOT NULL,       -- UsageType enum value
    quantity                BIGINT      NOT NULL CHECK (quantity >= 0),
    unit_cost_minor         BIGINT      NOT NULL CHECK (unit_cost_minor >= 0),
    total_cost_minor        BIGINT      NOT NULL CHECK (total_cost_minor >= 0),
    currency                CHAR(3)     NOT NULL DEFAULT 'INR',
    resource_id             TEXT        NOT NULL DEFAULT '',
    occurred_at_bucket      TEXT        NOT NULL,       -- ISO-8601 UTC hour bucket
    invoice_id              UUID,                       -- NULL until invoiced
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_usage_tenant_id         ON usage_events (tenant_id);
CREATE INDEX IF NOT EXISTS idx_usage_type_bucket       ON usage_events (tenant_id, usage_type, occurred_at_bucket);
CREATE INDEX IF NOT EXISTS idx_usage_invoice_id        ON usage_events (invoice_id);
CREATE INDEX IF NOT EXISTS idx_usage_uninvoiced        ON usage_events (tenant_id, invoice_id) WHERE invoice_id IS NULL;

-- Invoices
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    billing_period_start    TIMESTAMPTZ NOT NULL,
    billing_period_end      TIMESTAMPTZ NOT NULL,
    subtotal_minor          BIGINT      NOT NULL CHECK (subtotal_minor >= 0),
    tax_minor               BIGINT      NOT NULL DEFAULT 0 CHECK (tax_minor >= 0),
    total_minor             BIGINT      NOT NULL CHECK (total_minor >= 0),
    currency                CHAR(3)     NOT NULL DEFAULT 'INR',
    status                  TEXT        NOT NULL DEFAULT 'DRAFT',
    issued_at               TIMESTAMPTZ,
    paid_at                 TIMESTAMPTZ,
    due_date                TIMESTAMPTZ,
    payment_reference       TEXT        NOT NULL DEFAULT '',
    created_at              TIMESTAMPTZ NOT NULL,
    updated_at              TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_invoice_tenant_id       ON invoices (tenant_id);
CREATE INDEX IF NOT EXISTS idx_invoice_status          ON invoices (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_invoice_period          ON invoices (tenant_id, billing_period_start DESC);
