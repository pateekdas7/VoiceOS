"""ADR-005 Sec 3/4.2: platform_users (PlatformActor identity)

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-25

Deliberately has no tenant_id FK: a PlatformActor (VoiceOS's own staff) is
not a member of any tenant (ADR-005 Sec 3). This is a structurally separate
identity table from `users` (0003_users.py), not the same table with a
nullable tenant_id -- a compromised tenant admin credential must never be
able to escalate into platform-owner access by construction.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from scripts.db.migrations.alembic._ddl_helpers import add_enum_check, drop_check

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform_users (
            platform_user_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            email                    TEXT        NOT NULL,
            name                     TEXT        NOT NULL,
            platform_role            TEXT        NOT NULL,
            is_active                BOOLEAN     NOT NULL DEFAULT TRUE,
            mfa_enabled              BOOLEAN     NOT NULL DEFAULT FALSE,
            last_login_at            TIMESTAMPTZ,
            created_at               TIMESTAMPTZ NOT NULL,
            updated_at               TIMESTAMPTZ NOT NULL,

            CONSTRAINT uq_platform_user_email UNIQUE (email)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_platform_users_is_active ON platform_users (is_active);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_platform_users_platform_role ON platform_users (platform_role);")

    add_enum_check(
        "platform_users",
        "ck_platform_users_platform_role_enum",
        "platform_role",
        ("PLATFORM_ADMIN", "PLATFORM_SUPPORT", "PLATFORM_BILLING_OPS"),
    )


def downgrade() -> None:
    drop_check("platform_users", "ck_platform_users_platform_role_enum")
    op.execute("DROP TABLE IF EXISTS platform_users CASCADE;")
