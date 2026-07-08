"""Sprint-025: prompt_versions, model_configs, webhook_registrations,
webhook_deliveries, api_keys (V5 Ch13-16, Admin Portal / AI Configuration /
Integration Platform / API Platform)

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-06

Additive only -- no existing table is altered. ``model_configs`` uses the
same "two partial unique indexes distinguish tenant-default vs.
campaign-override rows" pattern as migration 0022's ``analytics_daily``
(a plain UNIQUE over a nullable ``campaign_id`` would treat every NULL as
distinct in Postgres, permitting duplicate tenant-default rows).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_versions (
            prompt_version_id  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            name               TEXT        NOT NULL,
            template           TEXT        NOT NULL,
            language           TEXT        NOT NULL DEFAULT 'en',
            version_number     INT         NOT NULL CHECK (version_number >= 1),
            hash               TEXT        NOT NULL,
            status             TEXT        NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'PUBLISHED')),
            created_by         TEXT        NOT NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            published_at       TIMESTAMPTZ,

            CONSTRAINT uq_prompt_versions_tenant_name_version UNIQUE (tenant_id, name, version_number)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_prompt_versions_tenant_id ON prompt_versions (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_prompt_versions_tenant_name ON prompt_versions (tenant_id, name);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_prompt_pins (
            campaign_id        UUID        PRIMARY KEY REFERENCES campaigns (campaign_id) ON DELETE CASCADE,
            tenant_id          UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            prompt_version_id  UUID        NOT NULL REFERENCES prompt_versions (prompt_version_id) ON DELETE RESTRICT,
            pinned_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_campaign_prompt_pins_tenant_id ON campaign_prompt_pins (tenant_id);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS model_configs (
            model_config_id    UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID             NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            campaign_id        UUID             REFERENCES campaigns (campaign_id) ON DELETE CASCADE,
            stt_adapter        TEXT             NOT NULL DEFAULT 'whisper',
            stt_model          TEXT             NOT NULL DEFAULT 'whisper-large-v3-turbo',
            llm_adapter        TEXT             NOT NULL DEFAULT 'vllm',
            llm_model          TEXT             NOT NULL DEFAULT 'qwen2.5-7b-instruct-fp8',
            llm_temperature    DOUBLE PRECISION NOT NULL DEFAULT 0.3 CHECK (llm_temperature >= 0 AND llm_temperature <= 2),
            tts_adapter        TEXT             NOT NULL DEFAULT 'veena',
            tts_voice          TEXT             NOT NULL DEFAULT 'kavya',
            created_at         TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
            updated_at         TIMESTAMPTZ      NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_model_configs_tenant_id ON model_configs (tenant_id);")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_model_configs_tenant_default "
        "ON model_configs (tenant_id) WHERE campaign_id IS NULL;"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_model_configs_tenant_campaign "
        "ON model_configs (tenant_id, campaign_id) WHERE campaign_id IS NOT NULL;"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_registrations (
            webhook_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            url                TEXT        NOT NULL,
            secret             TEXT        NOT NULL,
            event_types        TEXT[]      NOT NULL DEFAULT '{}',
            is_active          BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_webhook_registrations_tenant_id ON webhook_registrations (tenant_id);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_deliveries (
            delivery_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            webhook_id         UUID        NOT NULL REFERENCES webhook_registrations (webhook_id) ON DELETE CASCADE,
            tenant_id          UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            event_type         TEXT        NOT NULL,
            payload            JSONB       NOT NULL,
            attempt            INT         NOT NULL DEFAULT 0 CHECK (attempt >= 0),
            status             TEXT        NOT NULL DEFAULT 'PENDING'
                                CHECK (status IN ('PENDING', 'DELIVERED', 'RETRYING', 'DLQ')),
            last_error         TEXT        NOT NULL DEFAULT '',
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            delivered_at       TIMESTAMPTZ
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_webhook_id ON webhook_deliveries (webhook_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_tenant_id ON webhook_deliveries (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_status ON webhook_deliveries (status);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            api_key_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID        NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            key_hash           TEXT        NOT NULL UNIQUE,
            role               TEXT        NOT NULL DEFAULT '',
            scopes             TEXT[]      NOT NULL DEFAULT '{}',
            is_revoked         BOOLEAN     NOT NULL DEFAULT FALSE,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            revoked_at         TIMESTAMPTZ
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_tenant_id ON api_keys (tenant_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS api_keys CASCADE;")
    op.execute("DROP TABLE IF EXISTS webhook_deliveries CASCADE;")
    op.execute("DROP TABLE IF EXISTS webhook_registrations CASCADE;")
    op.execute("DROP TABLE IF EXISTS model_configs CASCADE;")
    op.execute("DROP TABLE IF EXISTS campaign_prompt_pins CASCADE;")
    op.execute("DROP TABLE IF EXISTS prompt_versions CASCADE;")
