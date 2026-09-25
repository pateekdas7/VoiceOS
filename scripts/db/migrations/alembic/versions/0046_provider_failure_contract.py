"""canonical provider failure fields

Revision ID: 0040
Revises: 0039
"""
from alembic import op
revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""-- Migration 040: canonical provider failure persistence on call attempts.
BEGIN;
ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS provider_failure_class TEXT;
ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS provider_failure_code TEXT;
ALTER TABLE call_attempts ADD COLUMN IF NOT EXISTS retryable BOOLEAN;
CREATE INDEX IF NOT EXISTS idx_call_attempts_provider_failure
  ON call_attempts (tenant_id, provider_failure_class, retryable);
COMMIT;""")

def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_call_attempts_provider_failure")
    op.execute("ALTER TABLE call_attempts DROP COLUMN IF EXISTS retryable")
    op.execute("ALTER TABLE call_attempts DROP COLUMN IF EXISTS provider_failure_code")
    op.execute("ALTER TABLE call_attempts DROP COLUMN IF EXISTS provider_failure_class")
