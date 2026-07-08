-- Migration 011: relationship_memory table
-- Sprint-010: RelationshipMemoryStore backing table
-- Architecture: V2 Ch12 (Relationship Memory)
-- Idempotent: uses IF NOT EXISTS

CREATE TABLE IF NOT EXISTS relationship_memory (
    customer_id          TEXT        PRIMARY KEY,
    total_calls          INTEGER     NOT NULL DEFAULT 0,
    ptp_history          JSONB       NOT NULL DEFAULT '[]',
    sentiment_history    JSONB       NOT NULL DEFAULT '[]',
    best_contact_time    TEXT        NOT NULL DEFAULT '',
    preferred_language   TEXT        NOT NULL DEFAULT 'hi-IN',
    escalation_count     INTEGER     NOT NULL DEFAULT 0,
    last_call_outcome    TEXT        NOT NULL DEFAULT '',
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for frequent queries by customer_id (covered by primary key)
-- Additional index on last_call_outcome for analytics (Sprint-024)
CREATE INDEX IF NOT EXISTS idx_relationship_memory_outcome
    ON relationship_memory (last_call_outcome);
