"""Add RECONCILING and extended statuses to call_attempts status check.

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-10

Migration 0036 created call_attempts with a narrow status check constraint.
The dialer_worker's CrashReconciler uses status='RECONCILING' as an atomic
claim lock (multi-worker safety) but this value was never added to the
constraint, causing reconciliation to fail with a check-constraint violation.
This migration extends the constraint to include RECONCILING plus the
additional status values (DIALING, RINGING, CANCELLED, VOICEMAIL) that the
worker emits during a live call lifecycle.
"""

from __future__ import annotations

from collections.abc import Sequence

import alembic.op as op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE call_attempts DROP CONSTRAINT IF EXISTS call_attempts_status_check")
    op.execute("""
        ALTER TABLE call_attempts
        ADD CONSTRAINT call_attempts_status_check
        CHECK (status = ANY (ARRAY[
            'INITIATED', 'DIALING', 'RINGING', 'IN_PROGRESS',
            'COMPLETED', 'FAILED', 'TIMEOUT', 'NO_ANSWER',
            'BUSY', 'CANCELLED', 'VOICEMAIL', 'RECONCILING'
        ]))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE call_attempts DROP CONSTRAINT IF EXISTS call_attempts_status_check")
    op.execute("""
        ALTER TABLE call_attempts
        ADD CONSTRAINT call_attempts_status_check
        CHECK (status IN ('INITIATED','IN_PROGRESS','COMPLETED','FAILED','TIMEOUT','NO_ANSWER','BUSY'))
    """)
