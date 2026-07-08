"""Sprint-014: organizations, business_units, branches (007_tenants_and_orgs.sql remainder)

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS organizations (
            org_id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            legal_name              TEXT        NOT NULL,
            country                 CHAR(2)     NOT NULL DEFAULT 'IN',
            registration_number     TEXT        NOT NULL DEFAULT '',
            created_at              TIMESTAMPTZ NOT NULL,
            updated_at              TIMESTAMPTZ NOT NULL,

            CONSTRAINT uq_org_tenant UNIQUE (tenant_id)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_org_tenant_id ON organizations (tenant_id);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS business_units (
            bu_id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id                  UUID        NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
            tenant_id               UUID        NOT NULL,
            name                    TEXT        NOT NULL,
            is_active               BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_bu_org_id ON business_units (org_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_bu_tenant_id ON business_units (tenant_id);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS branches (
            branch_id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            bu_id                   UUID        NOT NULL REFERENCES business_units (bu_id) ON DELETE CASCADE,
            tenant_id               UUID        NOT NULL,
            name                    TEXT        NOT NULL,
            city                    TEXT        NOT NULL DEFAULT '',
            state                   TEXT        NOT NULL DEFAULT '',
            is_active               BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_branch_bu_id ON branches (bu_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_branch_tenant_id ON branches (tenant_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS branches CASCADE;")
    op.execute("DROP TABLE IF EXISTS business_units CASCADE;")
    op.execute("DROP TABLE IF EXISTS organizations CASCADE;")
