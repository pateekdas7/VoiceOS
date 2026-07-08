"""Sprint-024: billing/usage-metering additive extensions

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-06

Extends the Sprint-014 billing schema rather than creating a parallel
``billing_invoices`` table (the sprint spec's name for what this repo
already built, since Sprint-014, as ``invoices`` — see CHANGELOG.md
Sprint-024 deviations):

- ``billing_subscriptions.tier``: adds ``TRIAL`` (Sprint-024's onboarding
  tier) to the existing STARTER/GROWTH/ENTERPRISE/ENTERPRISE_PLUS CHECK.
  The old constraint is dropped and re-added with the expanded value list
  (``add_enum_check``'s ``IF NOT EXISTS`` guard would otherwise silently
  keep the stale, narrower constraint in place).
- ``usage_events.usage_type``: adds ``STT_TOKEN``/``LLM_TOKEN``/
  ``GPU_SECOND`` (Sprint-024 per-model-stage usage dimensions) to the
  existing CALL_MINUTE/SMS_MESSAGE/API_CALL/STORAGE_MB/AI_TOKEN CHECK.
- ``invoices.line_items``: new JSONB column (default ``[]``) storing the
  computed ``InvoiceLineItem`` breakdown at generation time.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from scripts.db.migrations.alembic._ddl_helpers import add_enum_check, drop_check

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_OLD_TIER_VALUES = ("STARTER", "GROWTH", "ENTERPRISE", "ENTERPRISE_PLUS")
_NEW_TIER_VALUES = ("TRIAL", "STARTER", "GROWTH", "ENTERPRISE", "ENTERPRISE_PLUS")

_OLD_USAGE_TYPE_VALUES = ("CALL_MINUTE", "SMS_MESSAGE", "API_CALL", "STORAGE_MB", "AI_TOKEN")
_NEW_USAGE_TYPE_VALUES = (
    "CALL_MINUTE",
    "SMS_MESSAGE",
    "API_CALL",
    "STORAGE_MB",
    "AI_TOKEN",
    "STT_TOKEN",
    "LLM_TOKEN",
    "GPU_SECOND",
)


def upgrade() -> None:
    drop_check("billing_subscriptions", "ck_billing_subscriptions_tier_enum")
    add_enum_check("billing_subscriptions", "ck_billing_subscriptions_tier_enum", "tier", _NEW_TIER_VALUES)

    drop_check("usage_events", "ck_usage_events_type_enum")
    add_enum_check("usage_events", "ck_usage_events_type_enum", "usage_type", _NEW_USAGE_TYPE_VALUES)

    op.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS line_items JSONB NOT NULL DEFAULT '[]'::jsonb;")


def downgrade() -> None:
    op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS line_items;")

    drop_check("usage_events", "ck_usage_events_type_enum")
    add_enum_check("usage_events", "ck_usage_events_type_enum", "usage_type", _OLD_USAGE_TYPE_VALUES)

    drop_check("billing_subscriptions", "ck_billing_subscriptions_tier_enum")
    add_enum_check("billing_subscriptions", "ck_billing_subscriptions_tier_enum", "tier", _OLD_TIER_VALUES)
