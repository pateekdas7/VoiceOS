"""Sprint-014: roles, users, role_assignments (008_users_and_roles.sql)

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from scripts.db.migrations.alembic._ddl_helpers import add_enum_check, drop_check

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS roles (
            role_id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            name                    TEXT        NOT NULL,
            description             TEXT        NOT NULL DEFAULT '',
            permissions             TEXT[]      NOT NULL DEFAULT '{}',
            is_system_role          BOOLEAN     NOT NULL DEFAULT FALSE,
            created_at              TIMESTAMPTZ NOT NULL,
            updated_at              TIMESTAMPTZ NOT NULL,

            CONSTRAINT uq_role_tenant_name UNIQUE (tenant_id, name)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_roles_tenant_id ON roles (tenant_id);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            email                   TEXT        NOT NULL,
            name                    TEXT        NOT NULL,
            is_active               BOOLEAN     NOT NULL DEFAULT TRUE,
            is_service_account      BOOLEAN     NOT NULL DEFAULT FALSE,
            mfa_enabled             BOOLEAN     NOT NULL DEFAULT FALSE,
            last_login_at           TIMESTAMPTZ,
            created_at              TIMESTAMPTZ NOT NULL,
            updated_at              TIMESTAMPTZ NOT NULL,

            CONSTRAINT uq_user_tenant_email UNIQUE (tenant_id, email)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_tenant_id ON users (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_is_active ON users (tenant_id, is_active);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS role_assignments (
            assignment_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id                 UUID        NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
            role_id                 UUID        NOT NULL REFERENCES roles (role_id) ON DELETE CASCADE,
            scope_type              TEXT        NOT NULL,
            scope_id                TEXT        NOT NULL,
            assigned_by             UUID        NOT NULL,
            assigned_at             TIMESTAMPTZ NOT NULL,
            expires_at              TIMESTAMPTZ,

            CONSTRAINT uq_role_assignment UNIQUE (user_id, role_id, scope_type, scope_id)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_ra_user_id ON role_assignments (user_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ra_role_id ON role_assignments (role_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ra_scope ON role_assignments (scope_type, scope_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ra_expires_at ON role_assignments (expires_at);")

    add_enum_check(
        "role_assignments",
        "ck_role_assignments_scope_type_enum",
        "scope_type",
        ("TENANT", "ORG", "BUSINESS_UNIT", "BRANCH"),
    )


def downgrade() -> None:
    drop_check("role_assignments", "ck_role_assignments_scope_type_enum")
    op.execute("DROP TABLE IF EXISTS role_assignments CASCADE;")
    op.execute("DROP TABLE IF EXISTS users CASCADE;")
    op.execute("DROP TABLE IF EXISTS roles CASCADE;")
