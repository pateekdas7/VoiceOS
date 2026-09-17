-- Migration 005: Idempotency Keys
-- Architecture: V3 Ch8 (Idempotency); Invariant EV-7.
-- Idempotency keys prevent duplicate PTP recording, SMS sending, and payment
-- processing from retry / barge-in scenarios. Keys auto-expire via a
-- background job (or pg_cron in production).

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key                     TEXT        PRIMARY KEY,
    tenant_id               UUID        NOT NULL,
    resource_type           TEXT        NOT NULL,       -- 'ptp' | 'sms' | 'payment' | 'settlement' | etc.
    result                  JSONB,                      -- cached outcome for duplicate-key lookups (see 0009_idempotency_keys.py)
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at              TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_idem_tenant_id          ON idempotency_keys (tenant_id);
CREATE INDEX IF NOT EXISTS idx_idem_expires_at         ON idempotency_keys (expires_at);
-- Background cleanup: DELETE FROM idempotency_keys WHERE expires_at < NOW()
