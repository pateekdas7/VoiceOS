-- Migration 007: Tenants, Organisations, Business Units, and Branches
-- Architecture: V5 Ch2 (Tenant Management); V4 Ch6 (Multi-tenancy);
--               V7 Ch4 (Kubernetes Isolation).

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id               UUID        PRIMARY KEY,
    slug                    TEXT        NOT NULL UNIQUE,
    display_name            TEXT        NOT NULL,
    subscription_tier       TEXT        NOT NULL,
    isolation_profile       TEXT        NOT NULL DEFAULT 'SHARED',
    status                  TEXT        NOT NULL DEFAULT 'PROVISIONING',
    timezone                TEXT        NOT NULL DEFAULT 'Asia/Kolkata',
    currency                CHAR(3)     NOT NULL DEFAULT 'INR',
    max_concurrent_calls    INT         NOT NULL DEFAULT 10 CHECK (max_concurrent_calls >= 1),
    feature_flags           TEXT[]      NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ NOT NULL,
    updated_at              TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tenants_slug            ON tenants (slug);
CREATE INDEX IF NOT EXISTS idx_tenants_status          ON tenants (status);

-- Organisations
CREATE TABLE IF NOT EXISTS organizations (
    org_id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    legal_name              TEXT        NOT NULL,
    country                 CHAR(2)     NOT NULL DEFAULT 'IN',
    registration_number     TEXT        NOT NULL DEFAULT '',
    created_at              TIMESTAMPTZ NOT NULL,
    updated_at              TIMESTAMPTZ NOT NULL,

    CONSTRAINT uq_org_tenant UNIQUE (tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_org_tenant_id           ON organizations (tenant_id);

-- Business Units
CREATE TABLE IF NOT EXISTS business_units (
    bu_id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                  UUID        NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    tenant_id               UUID        NOT NULL,
    name                    TEXT        NOT NULL,
    is_active               BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bu_org_id               ON business_units (org_id);
CREATE INDEX IF NOT EXISTS idx_bu_tenant_id            ON business_units (tenant_id);

-- Branches
CREATE TABLE IF NOT EXISTS branches (
    branch_id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    bu_id                   UUID        NOT NULL REFERENCES business_units (bu_id) ON DELETE CASCADE,
    tenant_id               UUID        NOT NULL,
    name                    TEXT        NOT NULL,
    city                    TEXT        NOT NULL DEFAULT '',
    state                   TEXT        NOT NULL DEFAULT '',
    is_active               BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_branch_bu_id            ON branches (bu_id);
CREATE INDEX IF NOT EXISTS idx_branch_tenant_id        ON branches (tenant_id);
