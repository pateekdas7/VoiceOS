-- Migration 003: Promises to Pay, Settlements, Callbacks, and Escalations
-- Architecture: V5 Ch4.3 (PTP), V5 Ch4.4 (Settlement), V5 Ch4.5 (Callback),
--               V5 Ch4.6 (Escalation); V3 Ch8 (Idempotency).

CREATE TABLE IF NOT EXISTS promises_to_pay (
    ptp_id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL,
    call_id                 UUID        NOT NULL,
    customer_id             UUID        NOT NULL REFERENCES customers (customer_id),
    loan_account_id         TEXT        NOT NULL REFERENCES loan_accounts (loan_account_id),
    promised_amount_minor   BIGINT      NOT NULL CHECK (promised_amount_minor >= 0),
    currency                CHAR(3)     NOT NULL DEFAULT 'INR',
    promise_date            TIMESTAMPTZ NOT NULL,
    status                  TEXT        NOT NULL DEFAULT 'PENDING',
    notes                   TEXT        NOT NULL DEFAULT '',
    recorded_at             TIMESTAMPTZ NOT NULL,
    updated_at              TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ptp_tenant_id           ON promises_to_pay (tenant_id);
CREATE INDEX IF NOT EXISTS idx_ptp_customer_id         ON promises_to_pay (customer_id);
CREATE INDEX IF NOT EXISTS idx_ptp_loan_account        ON promises_to_pay (loan_account_id);
CREATE INDEX IF NOT EXISTS idx_ptp_call_id             ON promises_to_pay (call_id);
CREATE INDEX IF NOT EXISTS idx_ptp_status              ON promises_to_pay (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_ptp_promise_date        ON promises_to_pay (tenant_id, promise_date);

-- Settlements
CREATE TABLE IF NOT EXISTS settlements (
    settlement_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL,
    customer_id             UUID        NOT NULL REFERENCES customers (customer_id),
    loan_account_id         TEXT        NOT NULL REFERENCES loan_accounts (loan_account_id),
    settlement_amount_minor BIGINT      NOT NULL CHECK (settlement_amount_minor >= 0),
    waiver_amount_minor     BIGINT      NOT NULL DEFAULT 0 CHECK (waiver_amount_minor >= 0),
    currency                CHAR(3)     NOT NULL DEFAULT 'INR',
    offer_expiry            TIMESTAMPTZ NOT NULL,
    status                  TEXT        NOT NULL DEFAULT 'PROPOSED',
    proposed_at             TIMESTAMPTZ NOT NULL,
    updated_at              TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_settlement_tenant_id    ON settlements (tenant_id);
CREATE INDEX IF NOT EXISTS idx_settlement_customer_id  ON settlements (customer_id);
CREATE INDEX IF NOT EXISTS idx_settlement_status       ON settlements (tenant_id, status);

-- Callback requests
CREATE TABLE IF NOT EXISTS callback_requests (
    callback_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL,
    call_id                 UUID        NOT NULL,
    customer_id             UUID        NOT NULL REFERENCES customers (customer_id),
    loan_account_id         TEXT        NOT NULL REFERENCES loan_accounts (loan_account_id),
    preferred_time          TIMESTAMPTZ NOT NULL,
    timezone                TEXT        NOT NULL DEFAULT 'Asia/Kolkata',
    phone_number            TEXT        NOT NULL,
    is_fulfilled            BOOLEAN     NOT NULL DEFAULT FALSE,
    recorded_at             TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_callback_tenant_id      ON callback_requests (tenant_id);
CREATE INDEX IF NOT EXISTS idx_callback_customer_id    ON callback_requests (customer_id);
CREATE INDEX IF NOT EXISTS idx_callback_preferred_time ON callback_requests (tenant_id, preferred_time);
CREATE INDEX IF NOT EXISTS idx_callback_is_fulfilled   ON callback_requests (tenant_id, is_fulfilled);

-- Escalation records
CREATE TABLE IF NOT EXISTS escalation_records (
    escalation_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL,
    call_id                 UUID        NOT NULL,
    customer_id             UUID        NOT NULL REFERENCES customers (customer_id),
    reason                  TEXT        NOT NULL,
    escalated_to            TEXT        NOT NULL DEFAULT '',
    escalated_at            TIMESTAMPTZ NOT NULL,
    resolved_at             TIMESTAMPTZ,
    resolution_notes        TEXT        NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_escalation_tenant_id    ON escalation_records (tenant_id);
CREATE INDEX IF NOT EXISTS idx_escalation_customer_id  ON escalation_records (customer_id);
CREATE INDEX IF NOT EXISTS idx_escalation_call_id      ON escalation_records (call_id);
CREATE INDEX IF NOT EXISTS idx_escalation_resolved_at  ON escalation_records (tenant_id, resolved_at);
