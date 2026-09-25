-- Migration 040: canonical provider failure persistence on call attempts.
BEGIN;
ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS provider_failure_class TEXT;
ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS provider_failure_code TEXT;
ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS retryable BOOLEAN;
CREATE INDEX IF NOT EXISTS idx_call_attempts_provider_failure
  ON call_attempts (tenant_id, provider_failure_class, retryable);
COMMIT;