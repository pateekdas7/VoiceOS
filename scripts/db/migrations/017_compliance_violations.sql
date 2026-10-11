-- 017: compliance_violations — durable persistence for ComplianceMonitoring state (Phase 6d).
--
-- Replaces the in-process _violated_tenants set that is lost on service restart.
-- One row per (tenant_id, rule_id) pair; lifecycle: ACTIVE → RESOLVED → ACTIVE.
--
-- The uq_compliance_violations_tenant_rule UNIQUE constraint powers idempotent
-- ON CONFLICT upserts: ingest() never double-inserts a firing rule.

CREATE TABLE IF NOT EXISTS compliance_violations (
    violation_id    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    rule_id         TEXT        NOT NULL,
    signal_summary  TEXT        NOT NULL DEFAULT '',
    status          TEXT        NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'RESOLVED')),
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    redetected_at   TIMESTAMPTZ,
    CONSTRAINT uq_compliance_violations_tenant_rule UNIQUE (tenant_id, rule_id)
);

CREATE INDEX IF NOT EXISTS idx_compliance_violations_active
    ON compliance_violations (tenant_id, status)
    WHERE status = 'ACTIVE';

GRANT ALL PRIVILEGES ON TABLE compliance_violations TO voiceos;
