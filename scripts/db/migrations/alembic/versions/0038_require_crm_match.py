"""Add require_crm_match_before_dial flag to campaigns.

Revision ID: 0038
Revises: 0037
Create Date: 2026-09-17

When true, imported leads with UNMATCHED CRM status are not pushed to the Redis
dialer queue at import time.  Only MATCHED leads are queued; UNMATCHED/AMBIGUOUS
leads remain at queue_status='PENDING' until manually reviewed or the flag is
toggled off.

Rollback drops the column (safe — DEFAULT FALSE preserves existing behaviour).
"""

from __future__ import annotations

from collections.abc import Sequence

import alembic.op as op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE campaigns
        ADD COLUMN IF NOT EXISTS require_crm_match_before_dial BOOLEAN NOT NULL DEFAULT FALSE
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE campaigns DROP COLUMN IF EXISTS require_crm_match_before_dial")
