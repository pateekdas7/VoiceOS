"""Sprint-014: consents, consent_records (004_consents.sql)

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from scripts.db.migrations.alembic._ddl_helpers import add_enum_check, drop_check

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS consents (
            consent_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL,
            customer_id             UUID        NOT NULL REFERENCES customers (customer_id) ON DELETE CASCADE,
            consent_type            TEXT        NOT NULL,
            status                  TEXT        NOT NULL,
            granted_at              TIMESTAMPTZ,
            revoked_at              TIMESTAMPTZ,
            expires_at              TIMESTAMPTZ,
            created_at              TIMESTAMPTZ NOT NULL,
            updated_at              TIMESTAMPTZ NOT NULL,

            CONSTRAINT uq_consents_customer_type UNIQUE (customer_id, consent_type)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_consents_tenant_id ON consents (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_consents_customer_id ON consents (customer_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_consents_status ON consents (tenant_id, status);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_consents_type_status ON consents (customer_id, consent_type, status);")

    add_enum_check(
        "consents",
        "ck_consents_type_enum",
        "consent_type",
        ("CONTACT", "DATA_PROCESSING", "VOICE_RECORDING", "WHATSAPP", "EMAIL", "DATA_SHARING"),
    )
    add_enum_check(
        "consents",
        "ck_consents_status_enum",
        "status",
        ("GRANTED", "REVOKED", "PENDING", "EXPIRED"),
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS consent_records (
            record_id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            consent_id              UUID        NOT NULL REFERENCES consents (consent_id),
            action                  TEXT        NOT NULL,
            actor_id                TEXT        NOT NULL,
            channel                 TEXT        NOT NULL,
            recorded_at             TIMESTAMPTZ NOT NULL,
            ip_address              TEXT        NOT NULL DEFAULT '',
            notes                   TEXT        NOT NULL DEFAULT ''
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_consent_records_consent_id ON consent_records (consent_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_consent_records_recorded_at ON consent_records (recorded_at DESC);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS consent_records CASCADE;")
    drop_check("consents", "ck_consents_status_enum")
    drop_check("consents", "ck_consents_type_enum")
    op.execute("DROP TABLE IF EXISTS consents CASCADE;")
