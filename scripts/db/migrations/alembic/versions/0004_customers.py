"""Sprint-014: customers, contacts, addresses, parties (001_customers_and_parties.sql)

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS customers (
            customer_id         UUID        PRIMARY KEY,
            tenant_id           UUID        NOT NULL,
            crm_id              TEXT        NOT NULL,
            name                TEXT,
            preferred_language  TEXT        NOT NULL DEFAULT 'en',
            is_active           BOOLEAN     NOT NULL DEFAULT TRUE,
            data_erasure_requested BOOLEAN  NOT NULL DEFAULT FALSE,
            created_at          TIMESTAMPTZ NOT NULL,
            updated_at          TIMESTAMPTZ NOT NULL,

            CONSTRAINT uq_customers_tenant_crm UNIQUE (tenant_id, crm_id)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_customers_tenant_id ON customers (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_customers_crm_id ON customers (crm_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_customers_is_active ON customers (tenant_id, is_active);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_contacts (
            contact_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id         UUID        NOT NULL REFERENCES customers (customer_id) ON DELETE CASCADE,
            tenant_id           UUID        NOT NULL,
            contact_type        TEXT        NOT NULL,
            value               TEXT,
            is_primary          BOOLEAN     NOT NULL DEFAULT FALSE,
            is_dnc              BOOLEAN     NOT NULL DEFAULT FALSE,
            consent_captured    BOOLEAN     NOT NULL DEFAULT FALSE,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_contacts_customer_id ON customer_contacts (customer_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_contacts_tenant_id ON customer_contacts (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_contacts_is_dnc ON customer_contacts (tenant_id, is_dnc);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_contacts_tenant_value "
        "ON customer_contacts (tenant_id, value) WHERE value IS NOT NULL;"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_addresses (
            address_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id         UUID        NOT NULL REFERENCES customers (customer_id) ON DELETE CASCADE,
            tenant_id           UUID        NOT NULL,
            line1               TEXT,
            line2               TEXT,
            city                TEXT,
            state               TEXT,
            pincode             TEXT,
            country             CHAR(2)     NOT NULL DEFAULT 'IN',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_addresses_customer_id ON customer_addresses (customer_id);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS parties (
            party_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id         UUID        NOT NULL REFERENCES customers (customer_id) ON DELETE CASCADE,
            tenant_id           UUID        NOT NULL,
            role                TEXT        NOT NULL,
            name                TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_parties_customer_id ON parties (customer_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_parties_tenant_id ON parties (tenant_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS parties CASCADE;")
    op.execute("DROP TABLE IF EXISTS customer_addresses CASCADE;")
    op.execute("DROP TABLE IF EXISTS customer_contacts CASCADE;")
    op.execute("DROP TABLE IF EXISTS customers CASCADE;")
