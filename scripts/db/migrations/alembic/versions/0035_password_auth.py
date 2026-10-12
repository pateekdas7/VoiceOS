"""Add password_hash to platform_users and users for password-based login.

Revision ID: 0035
Revises: 0034
Create Date: 2026-07-30

Adds a nullable password_hash column to both identity tables.  Rows without a
hash are OAuth-only; rows with a hash support bcrypt password login via the BFF
/auth/password/login endpoint.  NULL means the account cannot use password auth.
"""

from __future__ import annotations

from collections.abc import Sequence

import alembic.op as op
import sqlalchemy as sa

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE platform_users ADD COLUMN IF NOT EXISTS password_hash TEXT;")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;")


def downgrade() -> None:
    op.execute("ALTER TABLE platform_users DROP COLUMN IF EXISTS password_hash;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS password_hash;")
