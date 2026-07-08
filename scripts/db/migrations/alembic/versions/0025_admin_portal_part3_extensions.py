"""Sprint-025 Part-3: webhook_delivery_attempts, webhook_dead_letter_queue,
api_key_usage, api_rate_limits (+seed), admin_audit_views (view), api_keys
expiration/plan-tier columns (V5 Ch13-16, Admin Portal / Integration
Platform / API Platform extensions)

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-07

Additive only -- migration 0024's tables/columns are never altered or
dropped. This migration adds:

  * ``webhook_delivery_attempts`` -- one row per individual HTTP delivery
    attempt (finer-grained than ``webhook_deliveries``' one-row-per-delivery
    summary, which is retained unchanged for backward compatibility).
    Append-only, enforced by a DB trigger mirroring migration 0010's
    ``audit_log_immutable()`` precedent -- the only existing trigger pattern
    in this codebase. Like ``audit_log``, ``tenant_id``/``webhook_id`` are
    plain UUID columns with no FK/CASCADE (not "``audit_log`` has no FK"
    happenstance -- an immutable table cannot have an ``ON DELETE CASCADE``
    pointing to it, since the cascade's own DELETE would hit the same
    BEFORE DELETE trigger and abort the parent's delete).
  * ``webhook_dead_letter_queue`` -- a dedicated, queryable DLQ store
    (supports future replay via ``replayed_at``), separate from
    ``webhook_deliveries.status = 'DLQ'``.
  * ``api_key_usage`` -- per-call usage log for API keys (API key lifecycle:
    "audit logging", "rate limit association").
  * ``api_rate_limits`` -- persisted per-tier rate-limit configuration
    (sustained rps + burst capacity), seeded from the values already
    hardcoded in ``src/services/api_platform/rate_limits.py``, so "billing
    plans determine API capabilities" is backed by real, tenant-editable
    Postgres rows instead of code constants.
  * ``admin_audit_views`` -- a read-only VIEW over ``audit_log`` scoped to
    Admin-Portal-originated actions, giving ``AuditAdminController`` a
    dedicated query surface without duplicating audit data.
  * ``api_keys.expires_at`` / ``api_keys.plan_tier`` -- additive columns for
    API key expiration and plan association.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # webhook_delivery_attempts -- per-attempt log, append-only
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_delivery_attempts (
            attempt_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            webhook_id         UUID        NOT NULL,
            tenant_id          UUID        NOT NULL,
            event_type         TEXT        NOT NULL,
            attempt_number     INT         NOT NULL CHECK (attempt_number >= 1),
            http_status        INT,
            succeeded          BOOLEAN     NOT NULL,
            error              TEXT        NOT NULL DEFAULT '',
            attempted_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_webhook_delivery_attempts_webhook_id "
        "ON webhook_delivery_attempts (webhook_id, attempted_at DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_webhook_delivery_attempts_tenant_id ON webhook_delivery_attempts (tenant_id);"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION webhook_delivery_attempts_immutable() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'webhook_delivery_attempts is append-only: % is not permitted', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_webhook_delivery_attempts_immutable ON webhook_delivery_attempts;")
    op.execute(
        """
        CREATE TRIGGER trg_webhook_delivery_attempts_immutable
            BEFORE UPDATE OR DELETE ON webhook_delivery_attempts
            FOR EACH ROW EXECUTE FUNCTION webhook_delivery_attempts_immutable();
        """
    )

    # ------------------------------------------------------------------
    # webhook_dead_letter_queue -- dedicated DLQ store, supports replay
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_dead_letter_queue (
            dlq_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            webhook_id         UUID        NOT NULL REFERENCES webhook_registrations (webhook_id) ON DELETE CASCADE,
            tenant_id          UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            event_type         TEXT        NOT NULL,
            payload            JSONB       NOT NULL,
            attempts           INT         NOT NULL CHECK (attempts >= 1),
            last_error         TEXT        NOT NULL DEFAULT '',
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            replayed_at        TIMESTAMPTZ
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_webhook_dlq_tenant_id ON webhook_dead_letter_queue (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_webhook_dlq_webhook_id ON webhook_dead_letter_queue (webhook_id);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_webhook_dlq_unreplayed ON webhook_dead_letter_queue (tenant_id) "
        "WHERE replayed_at IS NULL;"
    )

    # ------------------------------------------------------------------
    # api_key_usage -- per-call usage log
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS api_key_usage (
            usage_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            api_key_id         UUID        NOT NULL REFERENCES api_keys (api_key_id) ON DELETE CASCADE,
            tenant_id          UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            route              TEXT        NOT NULL,
            status_code        INT         NOT NULL CHECK (status_code >= 100 AND status_code < 600),
            occurred_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_key_usage_api_key_id ON api_key_usage (api_key_id, occurred_at DESC);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_api_key_usage_tenant_id ON api_key_usage (tenant_id, occurred_at DESC);")

    # ------------------------------------------------------------------
    # api_rate_limits -- persisted per-tier rate-limit configuration
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS api_rate_limits (
            tier               TEXT        PRIMARY KEY,
            requests_per_second INT        NOT NULL CHECK (requests_per_second > 0),
            burst_capacity     INT         NOT NULL CHECK (burst_capacity >= requests_per_second),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    # Seed data -- mirrors the pre-existing hardcoded TIER_RPS_LIMITS in
    # src/services/api_platform/rate_limits.py; burst_capacity allows a
    # short-window spike of up to 3x sustained rps (dual-window burst
    # handling, see rate_limits.py's BURST_WINDOW_SECONDS).
    op.execute(
        """
        INSERT INTO api_rate_limits (tier, requests_per_second, burst_capacity) VALUES
            ('TRIAL', 5, 15),
            ('STARTER', 20, 60),
            ('GROWTH', 100, 300),
            ('ENTERPRISE', 500, 1500),
            ('ENTERPRISE_PLUS', 2000, 6000)
        ON CONFLICT (tier) DO NOTHING;
        """
    )

    # ------------------------------------------------------------------
    # api_keys -- additive lifecycle columns (expiration, plan association)
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;")
    op.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS plan_tier TEXT NOT NULL DEFAULT '';")

    # ------------------------------------------------------------------
    # admin_audit_views -- read-only VIEW over audit_log, Admin-Portal-scoped
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW admin_audit_views AS
        SELECT audit_id, tenant_id, actor_id, action, resource_type, resource_id, outcome, recorded_at
        FROM audit_log
        WHERE resource_type = 'AdminAPI' OR left(action, 13) = 'admin_portal.';
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS admin_audit_views;")
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS plan_tier;")
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS expires_at;")
    op.execute("DROP TABLE IF EXISTS api_rate_limits CASCADE;")
    op.execute("DROP TABLE IF EXISTS api_key_usage CASCADE;")
    op.execute("DROP TABLE IF EXISTS webhook_dead_letter_queue CASCADE;")
    op.execute("DROP TRIGGER IF EXISTS trg_webhook_delivery_attempts_immutable ON webhook_delivery_attempts;")
    op.execute("DROP FUNCTION IF EXISTS webhook_delivery_attempts_immutable();")
    op.execute("DROP TABLE IF EXISTS webhook_delivery_attempts CASCADE;")
