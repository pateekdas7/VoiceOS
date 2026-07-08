-- Migration 002: Loan Accounts and EMI Schedule
-- Architecture: V5 Ch4 (Loan & Collections); Invariant RI-5 (Law of Authority)
-- All monetary amounts stored in minor currency units (paise/cents) to avoid
-- floating-point precision errors.

CREATE TABLE IF NOT EXISTS loan_accounts (
    loan_account_id         TEXT        PRIMARY KEY,    -- external lending system ID (authoritative)
    tenant_id               UUID        NOT NULL,
    customer_id             UUID        NOT NULL REFERENCES customers (customer_id),
    product_type            TEXT        NOT NULL,
    disbursed_amount_minor  BIGINT      NOT NULL CHECK (disbursed_amount_minor >= 0),
    currency                CHAR(3)     NOT NULL DEFAULT 'INR',
    interest_rate_bps       INT         NOT NULL CHECK (interest_rate_bps >= 0),
    tenure_months           INT         NOT NULL CHECK (tenure_months >= 1),
    disbursement_date       DATE        NOT NULL,
    maturity_date           DATE        NOT NULL,
    status                  TEXT        NOT NULL DEFAULT 'ACTIVE',
    dpd                     INT         NOT NULL DEFAULT 0 CHECK (dpd >= 0),
    -- Outstanding balance (denormalised for fast query — source of truth is CRM)
    outstanding_principal_minor BIGINT  DEFAULT 0 CHECK (outstanding_principal_minor >= 0),
    outstanding_interest_minor  BIGINT  DEFAULT 0 CHECK (outstanding_interest_minor >= 0),
    outstanding_penalty_minor   BIGINT  DEFAULT 0 CHECK (outstanding_penalty_minor >= 0),
    outstanding_total_minor     BIGINT  DEFAULT 0 CHECK (outstanding_total_minor >= 0),
    outstanding_as_of       TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL,
    updated_at              TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_loan_tenant_id          ON loan_accounts (tenant_id);
CREATE INDEX IF NOT EXISTS idx_loan_customer_id        ON loan_accounts (customer_id);
CREATE INDEX IF NOT EXISTS idx_loan_status             ON loan_accounts (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_loan_dpd                ON loan_accounts (tenant_id, dpd);

-- EMI schedule entries
CREATE TABLE IF NOT EXISTS emi_entries (
    emi_id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    loan_account_id         TEXT        NOT NULL REFERENCES loan_accounts (loan_account_id) ON DELETE CASCADE,
    tenant_id               UUID        NOT NULL,
    instalment_number       INT         NOT NULL CHECK (instalment_number >= 1),
    due_date                DATE        NOT NULL,
    principal_minor         BIGINT      NOT NULL CHECK (principal_minor >= 0),
    interest_minor          BIGINT      NOT NULL CHECK (interest_minor >= 0),
    total_minor             BIGINT      NOT NULL CHECK (total_minor >= 0),
    paid_minor              BIGINT      NOT NULL DEFAULT 0 CHECK (paid_minor >= 0),
    status                  TEXT        NOT NULL DEFAULT 'PENDING',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_emi_instalment UNIQUE (loan_account_id, instalment_number)
);

CREATE INDEX IF NOT EXISTS idx_emi_loan_account        ON emi_entries (loan_account_id);
CREATE INDEX IF NOT EXISTS idx_emi_due_date            ON emi_entries (tenant_id, due_date);
CREATE INDEX IF NOT EXISTS idx_emi_status              ON emi_entries (tenant_id, status);

-- DPD history snapshots
CREATE TABLE IF NOT EXISTS dpd_records (
    dpd_record_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    loan_account_id         TEXT        NOT NULL REFERENCES loan_accounts (loan_account_id) ON DELETE CASCADE,
    tenant_id               UUID        NOT NULL,
    dpd                     INT         NOT NULL CHECK (dpd >= 0),
    as_of_date              DATE        NOT NULL,
    overdue_minor           BIGINT      NOT NULL CHECK (overdue_minor >= 0),
    currency                CHAR(3)     NOT NULL DEFAULT 'INR',
    recorded_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dpd_loan_account        ON dpd_records (loan_account_id);
CREATE INDEX IF NOT EXISTS idx_dpd_as_of_date          ON dpd_records (tenant_id, as_of_date DESC);
