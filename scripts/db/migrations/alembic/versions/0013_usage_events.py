"""Sprint-014: usage_events, invoices (010_billing_and_usage.sql remainder)

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from scripts.db.migrations.alembic._ddl_helpers import add_enum_check, drop_check

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_events (
            usage_event_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            usage_type              TEXT        NOT NULL,
            quantity                BIGINT      NOT NULL CHECK (quantity >= 0),
            unit_cost_minor         BIGINT      NOT NULL CHECK (unit_cost_minor >= 0),
            total_cost_minor        BIGINT      NOT NULL CHECK (total_cost_minor >= 0),
            currency                CHAR(3)     NOT NULL DEFAULT 'INR',
            resource_id             TEXT        NOT NULL DEFAULT '',
            occurred_at_bucket      TEXT        NOT NULL,
            invoice_id              UUID,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_usage_tenant_id ON usage_events (tenant_id);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_type_bucket ON usage_events (tenant_id, usage_type, occurred_at_bucket);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_usage_invoice_id ON usage_events (invoice_id);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_uninvoiced ON usage_events (tenant_id, invoice_id) "
        "WHERE invoice_id IS NULL;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_tenant_occurred ON usage_events (tenant_id, occurred_at_bucket);"
    )

    add_enum_check(
        "usage_events",
        "ck_usage_events_type_enum",
        "usage_type",
        ("CALL_MINUTE", "SMS_MESSAGE", "API_CALL", "STORAGE_MB", "AI_TOKEN"),
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            billing_period_start    TIMESTAMPTZ NOT NULL,
            billing_period_end      TIMESTAMPTZ NOT NULL,
            subtotal_minor          BIGINT      NOT NULL CHECK (subtotal_minor >= 0),
            tax_minor               BIGINT      NOT NULL DEFAULT 0 CHECK (tax_minor >= 0),
            total_minor             BIGINT      NOT NULL CHECK (total_minor >= 0),
            currency                CHAR(3)     NOT NULL DEFAULT 'INR',
            status                  TEXT        NOT NULL DEFAULT 'DRAFT',
            issued_at               TIMESTAMPTZ,
            paid_at                 TIMESTAMPTZ,
            due_date                TIMESTAMPTZ,
            payment_reference       TEXT        NOT NULL DEFAULT '',
            created_at              TIMESTAMPTZ NOT NULL,
            updated_at              TIMESTAMPTZ NOT NULL
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_invoice_tenant_id ON invoices (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_invoice_status ON invoices (tenant_id, status);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_invoice_period ON invoices (tenant_id, billing_period_start DESC);")

    add_enum_check(
        "invoices",
        "ck_invoices_status_enum",
        "status",
        ("DRAFT", "ISSUED", "PAID", "OVERDUE", "VOID"),
    )


def downgrade() -> None:
    drop_check("invoices", "ck_invoices_status_enum")
    op.execute("DROP TABLE IF EXISTS invoices CASCADE;")
    drop_check("usage_events", "ck_usage_events_type_enum")
    op.execute("DROP TABLE IF EXISTS usage_events CASCADE;")
