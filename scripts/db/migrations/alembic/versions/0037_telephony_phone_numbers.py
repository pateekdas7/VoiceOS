"""Add tenant-scoped production telephony phone numbers."""
from __future__ import annotations
from collections.abc import Sequence
import alembic.op as op
revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS telephony_phone_numbers (
            phone_number_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            campaign_id UUID REFERENCES campaigns(campaign_id) ON DELETE SET NULL,
            provider TEXT NOT NULL CHECK (provider IN ('twilio','sip')),
            provider_number_id TEXT NOT NULL,
            e164_number TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','SUSPENDED','RELEASED')),
            inbound_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            outbound_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (provider, provider_number_id), UNIQUE (provider, e164_number)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_telephony_numbers_tenant ON telephony_phone_numbers (tenant_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_telephony_numbers_campaign ON telephony_phone_numbers (tenant_id, campaign_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_telephony_numbers_e164 ON telephony_phone_numbers (e164_number, status)")
    op.execute("GRANT ALL PRIVILEGES ON TABLE telephony_phone_numbers TO voiceos")

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS telephony_phone_numbers")
