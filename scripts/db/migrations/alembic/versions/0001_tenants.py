"""Sprint-014: tenants table (formalizes Sprint-002 007_tenants_and_orgs.sql)

Revision ID: 0001
Revises:
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from scripts.db.migrations.alembic._ddl_helpers import add_enum_check, drop_check

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenants (
            tenant_id               UUID        PRIMARY KEY,
            slug                    TEXT        NOT NULL UNIQUE,
            display_name            TEXT        NOT NULL,
            subscription_tier       TEXT        NOT NULL,
            isolation_profile       TEXT        NOT NULL DEFAULT 'SHARED',
            status                  TEXT        NOT NULL DEFAULT 'PROVISIONING',
            timezone                TEXT        NOT NULL DEFAULT 'Asia/Kolkata',
            currency                CHAR(3)     NOT NULL DEFAULT 'INR',
            max_concurrent_calls    INT         NOT NULL DEFAULT 10 CHECK (max_concurrent_calls >= 1),
            feature_flags           TEXT[]      NOT NULL DEFAULT '{}',
            created_at              TIMESTAMPTZ NOT NULL,
            updated_at              TIMESTAMPTZ NOT NULL
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_tenants_slug ON tenants (slug);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants (status);")

    add_enum_check(
        "tenants",
        "ck_tenants_status_enum",
        "status",
        ("PROVISIONING", "ACTIVE", "SUSPENDED", "DEPROVISIONING", "DELETED"),
    )
    add_enum_check(
        "tenants",
        "ck_tenants_isolation_profile_enum",
        "isolation_profile",
        ("SHARED", "DEDICATED_SCHEMA", "DEDICATED_CLUSTER"),
    )


def downgrade() -> None:
    drop_check("tenants", "ck_tenants_isolation_profile_enum")
    drop_check("tenants", "ck_tenants_status_enum")
    op.execute("DROP TABLE IF EXISTS tenants CASCADE;")
