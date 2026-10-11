"""Add DEFAULT NOW() to campaigns.created_at, updated_at, created_by.

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-10

Migration 0011 created the campaigns table with created_at/updated_at NOT NULL
but without DEFAULT clauses. Application code (bff.js) omits these columns in
INSERT statements and relies on DB defaults. This migration retroactively adds
the defaults so fresh staging/test databases match the production schema.
"""

from __future__ import annotations

from collections.abc import Sequence

import alembic.op as op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE campaigns
          ALTER COLUMN created_at SET DEFAULT NOW(),
          ALTER COLUMN updated_at SET DEFAULT NOW(),
          ALTER COLUMN created_by SET DEFAULT 'system'
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE campaigns
          ALTER COLUMN created_at DROP DEFAULT,
          ALTER COLUMN updated_at DROP DEFAULT,
          ALTER COLUMN created_by DROP DEFAULT
    """)
