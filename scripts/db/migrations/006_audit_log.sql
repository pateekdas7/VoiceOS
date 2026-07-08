-- Migration 006: Immutable Audit Log
-- Architecture: V4 Ch11 (Audit Trail); V4 Ch3 (AI Governance).
-- The audit_log table is append-only: no UPDATE or DELETE permitted in
-- application code. Rows are retained for the regulatory retention window
-- (V4 Ch11.3: 7 years for RBI; 5 years for DPDP).

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID        NOT NULL,
    actor_id                TEXT        NOT NULL,
    action                  TEXT        NOT NULL,       -- stable action code (e.g. 'customer.create')
    resource_type           TEXT        NOT NULL,
    resource_id             TEXT        NOT NULL,
    outcome                 TEXT        NOT NULL,       -- 'SUCCESS' | 'FAILURE' | 'PARTIAL'
    ip_address              TEXT        NOT NULL DEFAULT '',
    -- event_payload stores the JSON of the correlated DomainEvent (optional)
    event_payload           JSONB,
    recorded_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Audit log is partitioned by month in production (V7 Ch5).
-- For Sprint-002, we define the base table. Partitioning DDL is Sprint-023 scope.

CREATE INDEX IF NOT EXISTS idx_audit_tenant_id         ON audit_log (tenant_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor_id          ON audit_log (actor_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_resource          ON audit_log (resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_action            ON audit_log (action, recorded_at DESC);
