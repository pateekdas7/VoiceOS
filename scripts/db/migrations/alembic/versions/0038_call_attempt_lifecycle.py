"""Expand call-attempt lifecycle states for provider callbacks."""
from __future__ import annotations
from collections.abc import Sequence
import alembic.op as op
revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

def upgrade() -> None:
    op.execute("ALTER TABLE call_attempts DROP CONSTRAINT IF EXISTS call_attempts_status_check")
    op.execute("""
        ALTER TABLE call_attempts ADD CONSTRAINT call_attempts_status_check
        CHECK (status IN (
            'INITIATED','DIALING','RINGING','IN_PROGRESS','COMPLETED',
            'FAILED','TIMEOUT','NO_ANSWER','BUSY','CANCELLED','VOICEMAIL'
        ))
    """)

def downgrade() -> None:
    op.execute("ALTER TABLE call_attempts DROP CONSTRAINT IF EXISTS call_attempts_status_check")
    op.execute("""
        ALTER TABLE call_attempts ADD CONSTRAINT call_attempts_status_check
        CHECK (status IN ('INITIATED','IN_PROGRESS','COMPLETED','FAILED','TIMEOUT','NO_ANSWER','BUSY'))
    """)
