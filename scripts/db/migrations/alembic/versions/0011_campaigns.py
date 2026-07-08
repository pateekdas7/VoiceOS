"""Sprint-014: campaigns, ab_test_variants, call_dispositions (009_campaigns.sql)

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from scripts.db.migrations.alembic._ddl_helpers import add_enum_check, drop_check

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS campaigns (
            campaign_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            name                    TEXT        NOT NULL,
            description             TEXT        NOT NULL DEFAULT '',
            status                  TEXT        NOT NULL DEFAULT 'DRAFT',
            audience_criteria       JSONB       NOT NULL DEFAULT '{}',
            max_attempts            INT         NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
            retry_interval_hours    INT         NOT NULL DEFAULT 24 CHECK (retry_interval_hours >= 1),
            retry_on_outcomes       TEXT[]      NOT NULL DEFAULT '{}',
            do_not_retry_on_outcomes TEXT[]     NOT NULL DEFAULT '{}',
            scheduled_start         TIMESTAMPTZ,
            scheduled_end           TIMESTAMPTZ,
            daily_start_hour        SMALLINT    NOT NULL DEFAULT 9 CHECK (daily_start_hour BETWEEN 0 AND 23),
            daily_end_hour          SMALLINT    NOT NULL DEFAULT 18 CHECK (daily_end_hour BETWEEN 0 AND 23),
            timezone                TEXT        NOT NULL DEFAULT 'Asia/Kolkata',
            default_strategy        TEXT        NOT NULL DEFAULT '',
            target_call_count       INT         NOT NULL DEFAULT 0 CHECK (target_call_count >= 0),
            completed_call_count    INT         NOT NULL DEFAULT 0 CHECK (completed_call_count >= 0),
            created_at              TIMESTAMPTZ NOT NULL,
            updated_at              TIMESTAMPTZ NOT NULL,
            created_by              TEXT        NOT NULL
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_campaigns_tenant_id ON campaigns (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns (tenant_id, status);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_campaigns_scheduled ON campaigns (tenant_id, scheduled_start);")

    add_enum_check(
        "campaigns",
        "ck_campaigns_status_enum",
        "status",
        ("DRAFT", "SCHEDULED", "ACTIVE", "PAUSED", "COMPLETED", "CANCELLED"),
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ab_test_variants (
            variant_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            campaign_id             UUID        NOT NULL REFERENCES campaigns (campaign_id) ON DELETE CASCADE,
            tenant_id               UUID        NOT NULL,
            name                    TEXT        NOT NULL,
            strategy_override       TEXT        NOT NULL DEFAULT '',
            traffic_weight          SMALLINT    NOT NULL DEFAULT 50 CHECK (traffic_weight BETWEEN 1 AND 100),
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_ab_variant_campaign_id ON ab_test_variants (campaign_id);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS call_dispositions (
            disposition_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL,
            call_id                 UUID        NOT NULL,
            customer_id             UUID        NOT NULL REFERENCES customers (customer_id),
            loan_account_id         TEXT        NOT NULL REFERENCES loan_accounts (loan_account_id),
            outcome_code             TEXT        NOT NULL,
            duration_ms             INT         NOT NULL CHECK (duration_ms >= 0),
            dispositioned_at        TIMESTAMPTZ NOT NULL
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_disposition_tenant_id ON call_dispositions (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_disposition_customer_id ON call_dispositions (customer_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_disposition_call_id ON call_dispositions (call_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_disposition_outcome ON call_dispositions (tenant_id, outcome_code);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_disposition_date ON call_dispositions (tenant_id, dispositioned_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS call_dispositions CASCADE;")
    op.execute("DROP TABLE IF EXISTS ab_test_variants CASCADE;")
    drop_check("campaigns", "ck_campaigns_status_enum")
    op.execute("DROP TABLE IF EXISTS campaigns CASCADE;")
