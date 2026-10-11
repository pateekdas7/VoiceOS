"""System X — Autonomous Operations Controller tables (Sprint-028).

Creates four tables:
  system_x_incidents         — one row per correlated operational incident
  system_x_recovery_actions  — ordered recovery steps taken per incident
  system_x_audit_trail       — immutable log of every System X action
  system_x_notifications     — log of every notification sent

Architecture: System X (Autonomous Operations Controller).
"""

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    import psycopg2  # noqa: F401 — hints to alembic env that we use psycopg2 driver

    from alembic import op

    op.execute("""
        CREATE TABLE IF NOT EXISTS system_x_incidents (
            incident_id         TEXT PRIMARY KEY,
            title               TEXT NOT NULL,
            severity            TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'DETECTING',
            detected_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved_at         TIMESTAMPTZ,
            affected_services   TEXT[]  NOT NULL DEFAULT '{}',
            alert_fingerprints  TEXT[]  NOT NULL DEFAULT '{}',
            root_cause          TEXT,
            recovery_summary    TEXT,
            claude_analysis     JSONB   NOT NULL DEFAULT '{}',
            health_after        JSONB   NOT NULL DEFAULT '{}',
            total_downtime_s    INT,
            notifications_sent  TEXT[]  NOT NULL DEFAULT '{}',
            metadata            JSONB   NOT NULL DEFAULT '{}'
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS system_x_incidents_detected ON system_x_incidents(detected_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS system_x_incidents_status   ON system_x_incidents(status)")
    op.execute("CREATE INDEX IF NOT EXISTS system_x_incidents_severity ON system_x_incidents(severity, detected_at DESC)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS system_x_recovery_actions (
            action_id       TEXT PRIMARY KEY,
            incident_id     TEXT NOT NULL REFERENCES system_x_incidents(incident_id),
            action_type     TEXT NOT NULL,
            target_service  TEXT,
            status          TEXT NOT NULL DEFAULT 'PENDING',
            started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at    TIMESTAMPTZ,
            result          TEXT,
            error           TEXT,
            rolled_back     BOOLEAN NOT NULL DEFAULT FALSE,
            metadata        JSONB   NOT NULL DEFAULT '{}'
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS system_x_recovery_incident ON system_x_recovery_actions(incident_id, started_at)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS system_x_audit_trail (
            entry_id             TEXT PRIMARY KEY,
            incident_id          TEXT,
            recorded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            actor                TEXT NOT NULL,
            action               TEXT NOT NULL,
            result               TEXT,
            rollback_status      TEXT,
            verification_outcome TEXT,
            metadata             JSONB NOT NULL DEFAULT '{}'
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS system_x_audit_incident  ON system_x_audit_trail(incident_id, recorded_at)")
    op.execute("CREATE INDEX IF NOT EXISTS system_x_audit_recorded  ON system_x_audit_trail(recorded_at DESC)")
    # Immutability trigger — System X audit rows must never be updated or deleted
    op.execute("""
        CREATE OR REPLACE FUNCTION system_x_audit_immutable()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'system_x_audit_trail rows are immutable';
        END;
        $$
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS system_x_audit_no_mutate ON system_x_audit_trail;
        CREATE TRIGGER system_x_audit_no_mutate
        BEFORE UPDATE OR DELETE ON system_x_audit_trail
        FOR EACH ROW EXECUTE FUNCTION system_x_audit_immutable()
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS system_x_notifications (
            notification_id    TEXT PRIMARY KEY,
            incident_id        TEXT NOT NULL REFERENCES system_x_incidents(incident_id),
            channel            TEXT NOT NULL,
            notification_type  TEXT NOT NULL,
            recipient          TEXT NOT NULL,
            subject            TEXT,
            body               TEXT NOT NULL,
            status             TEXT NOT NULL DEFAULT 'pending',
            sent_at            TIMESTAMPTZ,
            error              TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS system_x_notif_incident ON system_x_notifications(incident_id)")
    op.execute("CREATE INDEX IF NOT EXISTS system_x_notif_status   ON system_x_notifications(status, created_at)")


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS system_x_notifications")
    op.execute("DROP TABLE IF EXISTS system_x_audit_trail")
    op.execute("DROP TABLE IF EXISTS system_x_recovery_actions")
    op.execute("DROP TABLE IF EXISTS system_x_incidents")
    op.execute("DROP FUNCTION IF EXISTS system_x_audit_immutable")
