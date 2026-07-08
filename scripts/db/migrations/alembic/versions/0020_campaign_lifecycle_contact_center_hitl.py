"""Sprint-023: campaign lifecycle rework, campaign_audiences, campaign_results, hitl_queue, hitl_decisions

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-06

``campaigns.status``: migration 0011 (Sprint-014) shipped a 6-value CHECK
(``DRAFT``/``SCHEDULED``/``ACTIVE``/``PAUSED``/``COMPLETED``/``CANCELLED``)
that predates Sprint-023's actual lifecycle spec: ``DRAFT -> REVIEW ->
APPROVED -> ACTIVE -> PAUSED -> COMPLETED -> ARCHIVED``. Zero campaign rows
exist anywhere (CampaignManagementService does not exist until this sprint),
so this revision drops the old CHECK and installs the new one directly — no
value-mapping UPDATE is needed for the (empty) upgrade path, but one is
provided for symmetry/safety in case a row was created directly via SQL
(``SCHEDULED``->``REVIEW``, ``CANCELLED``->``ARCHIVED``, same
"map-then-swap-constraint" precedent as migration 0018's tenant status
rework).

New tables (all net-new — grepped 0001-0019 and found no prior definition):
- ``campaign_audiences``: the materialized cohort for a campaign (audience
  selection output), one row per (campaign_id, customer_id) with the
  assigned A/B variant and DND/exclusion state.
- ``campaign_results``: per-contact-attempt outcome, feeding A/B variant
  metrics (PTP rate, completion rate).
- ``hitl_queue``: durable Postgres-backed queue for REQUIRE_HUMAN verdicts
  (V4 Ch15) — survives restarts, unlike Sprint-020's in-memory
  ``HumanOversightRouter._queue``. ``call_id`` is ``TEXT`` (not ``UUID``,
  unlike ``call_dispositions``/``promises_to_pay``) — found during Phase 2
  real-Postgres validation: ``ScheduleEngine`` and other pre-dial call
  admission checks build synthetic, non-UUID call identifiers (e.g.
  ``f"{campaign_id}:{customer_id}"``) before a real call session exists,
  so this column cannot assume UUID shape. Same precedent as migration
  0014's ``snapshots``/``recovery_log`` tables.
- ``hitl_decisions``: immutable record of every supervisor decision
  (mandatory rationale + identity, V4 Ch15 §15.12/§15.17).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from scripts.db.migrations.alembic._ddl_helpers import add_enum_check, drop_check

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_OLD_CAMPAIGN_STATUS_VALUES = ("DRAFT", "SCHEDULED", "ACTIVE", "PAUSED", "COMPLETED", "CANCELLED")
_NEW_CAMPAIGN_STATUS_VALUES = ("DRAFT", "REVIEW", "APPROVED", "ACTIVE", "PAUSED", "COMPLETED", "ARCHIVED")

_UPGRADE_STATUS_MAP = {"SCHEDULED": "REVIEW", "CANCELLED": "ARCHIVED"}
_DOWNGRADE_STATUS_MAP = {"REVIEW": "SCHEDULED", "APPROVED": "SCHEDULED", "ARCHIVED": "CANCELLED"}


def upgrade() -> None:
    drop_check("campaigns", "ck_campaigns_status_enum")
    for old_value, new_value in _UPGRADE_STATUS_MAP.items():
        op.execute(f"UPDATE campaigns SET status = '{new_value}' WHERE status = '{old_value}';")
    add_enum_check("campaigns", "ck_campaigns_status_enum", "status", _NEW_CAMPAIGN_STATUS_VALUES)

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_audiences (
            campaign_audience_id    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            campaign_id             UUID        NOT NULL REFERENCES campaigns (campaign_id) ON DELETE CASCADE,
            tenant_id               UUID        NOT NULL,
            customer_id             UUID        NOT NULL REFERENCES customers (customer_id),
            loan_account_id         TEXT        REFERENCES loan_accounts (loan_account_id),
            variant_id              UUID        REFERENCES ab_test_variants (variant_id),
            dnd                     BOOLEAN     NOT NULL DEFAULT FALSE,
            included_at             TIMESTAMPTZ NOT NULL,
            excluded_reason         TEXT        NOT NULL DEFAULT '',

            CONSTRAINT uq_campaign_audience_member UNIQUE (campaign_id, customer_id)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_campaign_audience_tenant_id ON campaign_audiences (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_campaign_audience_campaign_id ON campaign_audiences (campaign_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_campaign_audience_customer_id ON campaign_audiences (customer_id);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_results (
            campaign_result_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            campaign_id             UUID        NOT NULL REFERENCES campaigns (campaign_id) ON DELETE CASCADE,
            tenant_id               UUID        NOT NULL,
            variant_id              UUID        REFERENCES ab_test_variants (variant_id),
            customer_id             UUID        NOT NULL REFERENCES customers (customer_id),
            call_id                 UUID,
            outcome_code            TEXT        NOT NULL,
            ptp_created             BOOLEAN     NOT NULL DEFAULT FALSE,
            completed_at            TIMESTAMPTZ NOT NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_campaign_result_tenant_id ON campaign_results (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_campaign_result_campaign_id ON campaign_results (campaign_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_campaign_result_variant_id ON campaign_results (variant_id);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hitl_queue (
            hitl_item_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL,
            call_id                 TEXT        NOT NULL,
            reason                  TEXT        NOT NULL,
            priority                TEXT        NOT NULL DEFAULT 'MEDIUM',
            status                  TEXT        NOT NULL DEFAULT 'PENDING',
            context                 JSONB       NOT NULL DEFAULT '{}',
            enqueued_at             TIMESTAMPTZ NOT NULL,
            claimed_by              TEXT,
            claimed_at              TIMESTAMPTZ,
            resolved_at             TIMESTAMPTZ,
            sla_deadline_at         TIMESTAMPTZ NOT NULL,
            sla_breached            BOOLEAN     NOT NULL DEFAULT FALSE
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_hitl_queue_tenant_id ON hitl_queue (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_hitl_queue_status ON hitl_queue (tenant_id, status);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_hitl_queue_sla_deadline ON hitl_queue (sla_deadline_at);")
    add_enum_check("hitl_queue", "ck_hitl_queue_priority_enum", "priority", ("CRITICAL", "HIGH", "MEDIUM"))
    add_enum_check("hitl_queue", "ck_hitl_queue_status_enum", "status", ("PENDING", "CLAIMED", "RESOLVED"))

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hitl_decisions (
            hitl_decision_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            hitl_item_id            UUID        NOT NULL REFERENCES hitl_queue (hitl_item_id) ON DELETE CASCADE,
            tenant_id               UUID        NOT NULL,
            supervisor_id           TEXT        NOT NULL,
            decision                TEXT        NOT NULL,
            rationale               TEXT        NOT NULL,
            decided_at              TIMESTAMPTZ NOT NULL
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_hitl_decision_tenant_id ON hitl_decisions (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_hitl_decision_item_id ON hitl_decisions (hitl_item_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS hitl_decisions CASCADE;")

    drop_check("hitl_queue", "ck_hitl_queue_status_enum")
    drop_check("hitl_queue", "ck_hitl_queue_priority_enum")
    op.execute("DROP TABLE IF EXISTS hitl_queue CASCADE;")

    op.execute("DROP TABLE IF EXISTS campaign_results CASCADE;")
    op.execute("DROP TABLE IF EXISTS campaign_audiences CASCADE;")

    drop_check("campaigns", "ck_campaigns_status_enum")
    for old_value, new_value in _DOWNGRADE_STATUS_MAP.items():
        op.execute(f"UPDATE campaigns SET status = '{new_value}' WHERE status = '{old_value}';")
    add_enum_check("campaigns", "ck_campaigns_status_enum", "status", _OLD_CAMPAIGN_STATUS_VALUES)
