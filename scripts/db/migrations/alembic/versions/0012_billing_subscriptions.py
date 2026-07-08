"""Sprint-014: billing_subscriptions (010_billing_and_usage.sql, subscriptions part)

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from scripts.db.migrations.alembic._ddl_helpers import (
    add_enum_check,
    add_unique_if_missing,
    drop_check,
    drop_constraint,
)

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS billing_subscriptions (
            subscription_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            tier                    TEXT        NOT NULL,
            rate_card_version       TEXT        NOT NULL,
            contract_start          TIMESTAMPTZ NOT NULL,
            contract_end            TIMESTAMPTZ,
            base_fee_minor          BIGINT      NOT NULL DEFAULT 0 CHECK (base_fee_minor >= 0),
            currency                CHAR(3)     NOT NULL DEFAULT 'INR',
            is_active               BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at              TIMESTAMPTZ NOT NULL,
            updated_at              TIMESTAMPTZ NOT NULL
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_sub_tenant_id ON billing_subscriptions (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_sub_is_active ON billing_subscriptions (tenant_id, is_active);")

    # One active subscription per tenant (Sprint-014.md: tenant_id UUID FK UNIQUE). Added as a
    # standalone guarded ALTER, not inline in CREATE TABLE, because billing_subscriptions already
    # exists on any database provisioned by Sprint-002.
    add_unique_if_missing("billing_subscriptions", "uq_billing_subscriptions_tenant", ("tenant_id",))

    add_enum_check(
        "billing_subscriptions",
        "ck_billing_subscriptions_tier_enum",
        "tier",
        ("STARTER", "GROWTH", "ENTERPRISE", "ENTERPRISE_PLUS"),
    )


def downgrade() -> None:
    drop_check("billing_subscriptions", "ck_billing_subscriptions_tier_enum")
    drop_constraint("billing_subscriptions", "uq_billing_subscriptions_tenant")
    op.execute("DROP TABLE IF EXISTS billing_subscriptions CASCADE;")
