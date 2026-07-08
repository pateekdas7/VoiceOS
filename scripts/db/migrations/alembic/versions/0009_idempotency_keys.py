"""Sprint-014: idempotency_keys (005_idempotency_keys.sql + additive `result` column)

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-04

The Sprint-002 table has no ``result`` column, so a duplicate-key lookup could
confirm a key was already used but had nowhere to return the cached outcome
from. This is an additive (expand-only) column: nullable, no rewrite of
existing rows required, existing readers unaffected. It exists to support
``IdempotencyRepository.check()`` (Sprint-014 AC) and ``IdempotencyGuard``
(Sprint-015).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            key                     TEXT        PRIMARY KEY,
            tenant_id               UUID        NOT NULL,
            resource_type           TEXT        NOT NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at              TIMESTAMPTZ NOT NULL
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_idem_tenant_id ON idempotency_keys (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_idem_expires_at ON idempotency_keys (expires_at);")

    # Expand: add nullable `result` column (V6 Ch7 expand-contract — additive only).
    op.execute("ALTER TABLE idempotency_keys ADD COLUMN IF NOT EXISTS result JSONB;")


def downgrade() -> None:
    op.execute("ALTER TABLE idempotency_keys DROP COLUMN IF EXISTS result;")
    op.execute("DROP TABLE IF EXISTS idempotency_keys CASCADE;")
