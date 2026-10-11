"""Create leads, pipelines, pipeline_stages, lead_imports and supporting tables.

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-10

These tables exist in production but were never added to the Alembic migration chain.
They back the lead upload, pipeline management, enrichment, and execution event logging
features of bff.js. Staging databases built from migrations need them to pass E2E tests.

Tables added (in creation order that resolves circular FK between pipelines and leads):
  pipelines, pipeline_stages, lead_imports, leads,
  pipeline_distribution_rules, lead_enrichment_log, lead_execution_events,
  campaign_qualification_rules

The circular FK (pipelines.current_lead_id → leads) is added via ALTER TABLE after
leads is created.
"""

from __future__ import annotations

from collections.abc import Sequence

import alembic.op as op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Ensure customers has the composite unique needed for the leads FK.
    # Production already has this; staging migrations were missing it.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_customers_customer_tenant
            ON customers (customer_id, tenant_id)
    """)

    op.execute("""
        CREATE TABLE pipelines (
            pipeline_id      UUID        NOT NULL DEFAULT gen_random_uuid(),
            tenant_id        UUID        NOT NULL,
            campaign_id      UUID        NOT NULL,
            name             TEXT        NOT NULL,
            status           TEXT        NOT NULL DEFAULT 'DRAFT',
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by       TEXT        NOT NULL DEFAULT 'system',
            current_lead_id  UUID,
            current_call_sid TEXT,
            calls_completed  INTEGER     NOT NULL DEFAULT 0,
            calls_no_answer  INTEGER     NOT NULL DEFAULT 0,
            calls_failed     INTEGER     NOT NULL DEFAULT 0,
            total_duration_s INTEGER     NOT NULL DEFAULT 0,
            runtime_status   TEXT        NOT NULL DEFAULT 'IDLE',
            CONSTRAINT pipelines_pkey PRIMARY KEY (pipeline_id),
            CONSTRAINT pipelines_business_status_check
                CHECK (status IN ('DRAFT','ACTIVE','PAUSED','ARCHIVED')),
            CONSTRAINT pipelines_runtime_status_check
                CHECK (runtime_status IN ('IDLE','BUSY')),
            CONSTRAINT uq_pipelines_scope UNIQUE (pipeline_id, tenant_id, campaign_id),
            CONSTRAINT pipelines_campaign_id_fkey
                FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
            CONSTRAINT pipelines_tenant_id_fkey
                FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
        )
    """)

    op.execute("""
        CREATE INDEX idx_pipelines_campaign_id ON pipelines (tenant_id, campaign_id);
        CREATE INDEX idx_pipelines_status      ON pipelines (tenant_id, status);
        CREATE INDEX idx_pipelines_tenant_id   ON pipelines (tenant_id);
    """)

    op.execute("""
        CREATE TABLE pipeline_stages (
            stage_id    UUID        NOT NULL DEFAULT gen_random_uuid(),
            pipeline_id UUID        NOT NULL,
            tenant_id   UUID        NOT NULL,
            campaign_id UUID        NOT NULL,
            name        TEXT        NOT NULL,
            position    INTEGER     NOT NULL,
            created_by  TEXT        NOT NULL DEFAULT '',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT pipeline_stages_pkey PRIMARY KEY (stage_id),
            CONSTRAINT pipeline_stages_name_check
                CHECK (length(btrim(name)) >= 1 AND length(btrim(name)) <= 120),
            CONSTRAINT pipeline_stages_position_check
                CHECK (position >= 0),
            CONSTRAINT uq_pipeline_stages_order UNIQUE (pipeline_id, position),
            CONSTRAINT uq_pipeline_stages_scope UNIQUE (stage_id, pipeline_id, tenant_id, campaign_id),
            CONSTRAINT fk_pipeline_stages_scope
                FOREIGN KEY (pipeline_id, tenant_id, campaign_id)
                REFERENCES pipelines(pipeline_id, tenant_id, campaign_id) ON DELETE CASCADE
        )
    """)

    op.execute("""
        CREATE UNIQUE INDEX uq_pipeline_stage_name ON pipeline_stages (pipeline_id, lower(name));
        CREATE INDEX idx_pipeline_stages_scope
            ON pipeline_stages (tenant_id, campaign_id, pipeline_id, position);
    """)

    op.execute("""
        CREATE TABLE lead_imports (
            import_id          UUID        NOT NULL DEFAULT gen_random_uuid(),
            tenant_id          UUID        NOT NULL,
            campaign_id        UUID        NOT NULL,
            filename           TEXT        NOT NULL,
            status             TEXT        NOT NULL DEFAULT 'PROCESSING',
            total_rows         INTEGER     NOT NULL DEFAULT 0,
            valid_rows         INTEGER     NOT NULL DEFAULT 0,
            invalid_rows       INTEGER     NOT NULL DEFAULT 0,
            duplicate_rows     INTEGER     NOT NULL DEFAULT 0,
            original_columns   JSONB       NOT NULL DEFAULT '[]',
            column_mapping     JSONB       NOT NULL DEFAULT '{}',
            rows_data          JSONB       NOT NULL DEFAULT '[]',
            last_processed_row INTEGER     NOT NULL DEFAULT 0,
            failed_rows        JSONB       NOT NULL DEFAULT '[]',
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at       TIMESTAMPTZ,
            CONSTRAINT lead_imports_pkey PRIMARY KEY (import_id),
            CONSTRAINT lead_imports_status_check
                CHECK (status IN ('PROCESSING','DONE','FAILED')),
            CONSTRAINT lead_imports_total_rows_check     CHECK (total_rows     >= 0),
            CONSTRAINT lead_imports_valid_rows_check     CHECK (valid_rows     >= 0),
            CONSTRAINT lead_imports_invalid_rows_check   CHECK (invalid_rows   >= 0),
            CONSTRAINT lead_imports_duplicate_rows_check CHECK (duplicate_rows >= 0),
            CONSTRAINT lead_imports_campaign_id_fkey
                FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
            CONSTRAINT lead_imports_tenant_id_fkey
                FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
        )
    """)

    op.execute("""
        CREATE INDEX idx_lead_imports_campaign_id ON lead_imports (tenant_id, campaign_id);
        CREATE INDEX idx_lead_imports_tenant_id   ON lead_imports (tenant_id);
    """)

    op.execute(r"""
        CREATE TABLE leads (
            lead_id           UUID        NOT NULL DEFAULT gen_random_uuid(),
            campaign_id       UUID        NOT NULL,
            pipeline_id       UUID,
            tenant_id         UUID        NOT NULL,
            import_id         UUID,
            name              TEXT        NOT NULL DEFAULT '',
            phone             TEXT        NOT NULL,
            email             TEXT,
            language          TEXT        NOT NULL DEFAULT 'HINDI',
            score             SMALLINT    NOT NULL DEFAULT 0,
            qualified         BOOLEAN     NOT NULL DEFAULT false,
            status            TEXT        NOT NULL DEFAULT 'PENDING',
            queue_status      TEXT        NOT NULL DEFAULT 'PENDING',
            rejection_reason  TEXT,
            metadata          JSONB       NOT NULL DEFAULT '{}',
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            pipeline_stage_id UUID,
            crm_customer_id   UUID,
            priority          INTEGER     NOT NULL DEFAULT 2,
            CONSTRAINT leads_pkey PRIMARY KEY (lead_id),
            CONSTRAINT leads_campaign_id_phone_key UNIQUE (campaign_id, phone),
            CONSTRAINT ck_leads_phone_e164
                CHECK (phone ~ '^\+91[6-9][0-9]{9}$'),
            CONSTRAINT leads_score_check
                CHECK (score >= 0 AND score <= 100),
            CONSTRAINT leads_status_check
                CHECK (status IN ('PENDING','VALIDATED','QUALIFIED','ASSIGNED','CALLED','COMPLETED','REJECTED')),
            CONSTRAINT leads_queue_status_check
                CHECK (queue_status IN ('PENDING','QUEUED','IN_CALL','DONE','FAILED')),
            CONSTRAINT leads_campaign_id_fkey
                FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
            CONSTRAINT leads_tenant_id_fkey
                FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            CONSTRAINT leads_pipeline_id_fkey
                FOREIGN KEY (pipeline_id) REFERENCES pipelines(pipeline_id) ON DELETE SET NULL,
            CONSTRAINT leads_import_id_fkey
                FOREIGN KEY (import_id) REFERENCES lead_imports(import_id) ON DELETE SET NULL,
            CONSTRAINT fk_leads_pipeline_stage_scope
                FOREIGN KEY (pipeline_stage_id, pipeline_id, tenant_id, campaign_id)
                REFERENCES pipeline_stages(stage_id, pipeline_id, tenant_id, campaign_id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_leads_crm_customer_tenant
                FOREIGN KEY (crm_customer_id, tenant_id)
                REFERENCES customers(customer_id, tenant_id) ON DELETE RESTRICT
        )
    """)

    op.execute("""
        CREATE INDEX idx_leads_queue
            ON leads (tenant_id, queue_status, status);
        CREATE INDEX idx_leads_pipeline_stage
            ON leads (tenant_id, pipeline_id, pipeline_stage_id);
        CREATE INDEX idx_leads_crm_customer
            ON leads (tenant_id, crm_customer_id) WHERE crm_customer_id IS NOT NULL;
        CREATE INDEX leads_priority_idx
            ON leads (priority, created_at) WHERE queue_status = 'PENDING';
    """)

    op.execute("""
        ALTER TABLE pipelines
            ADD CONSTRAINT pipelines_current_lead_id_fkey
                FOREIGN KEY (current_lead_id) REFERENCES leads(lead_id) ON DELETE SET NULL
    """)

    op.execute("""
        CREATE TABLE pipeline_distribution_rules (
            rule_id     UUID        NOT NULL DEFAULT gen_random_uuid(),
            tenant_id   UUID        NOT NULL,
            campaign_id UUID        NOT NULL,
            pipeline_id UUID        NOT NULL,
            min_score   SMALLINT    NOT NULL DEFAULT 0,
            max_score   SMALLINT    NOT NULL DEFAULT 100,
            languages   TEXT[]      NOT NULL DEFAULT '{}',
            priority    SMALLINT    NOT NULL DEFAULT 0,
            is_active   BOOLEAN     NOT NULL DEFAULT true,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT pipeline_distribution_rules_pkey PRIMARY KEY (rule_id),
            CONSTRAINT pipeline_distribution_rules_min_score_check
                CHECK (min_score >= 0 AND min_score <= 100),
            CONSTRAINT pipeline_distribution_rules_max_score_check
                CHECK (max_score >= 0 AND max_score <= 100),
            CONSTRAINT pipeline_distribution_rules_check
                CHECK (max_score >= min_score),
            CONSTRAINT pipeline_distribution_rules_campaign_id_fkey
                FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
            CONSTRAINT pipeline_distribution_rules_tenant_id_fkey
                FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            CONSTRAINT pipeline_distribution_rules_pipeline_id_fkey
                FOREIGN KEY (pipeline_id) REFERENCES pipelines(pipeline_id) ON DELETE CASCADE
        )
    """)

    op.execute("""
        CREATE INDEX idx_pdr_campaign ON pipeline_distribution_rules (tenant_id, campaign_id, is_active);
    """)

    op.execute("""
        CREATE TABLE lead_enrichment_log (
            log_id     UUID        NOT NULL DEFAULT gen_random_uuid(),
            lead_id    UUID,
            lead_phone TEXT        NOT NULL DEFAULT '',
            provider   TEXT        NOT NULL,
            result     JSONB       NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT lead_enrichment_log_pkey PRIMARY KEY (log_id),
            CONSTRAINT lead_enrichment_log_lead_id_fkey
                FOREIGN KEY (lead_id) REFERENCES leads(lead_id) ON DELETE SET NULL
        )
    """)

    op.execute("""
        CREATE INDEX idx_enrich_log_lead    ON lead_enrichment_log (lead_id);
        CREATE INDEX idx_enrich_log_phone   ON lead_enrichment_log (lead_phone);
        CREATE INDEX idx_enrich_log_created ON lead_enrichment_log (created_at DESC);
    """)

    op.execute("""
        CREATE TABLE lead_execution_events (
            event_id    UUID        NOT NULL DEFAULT gen_random_uuid(),
            lead_id     UUID,
            campaign_id UUID        NOT NULL,
            pipeline_id UUID,
            tenant_id   UUID        NOT NULL,
            event_type  TEXT        NOT NULL,
            status      TEXT        NOT NULL DEFAULT 'SUCCESS',
            message     TEXT        NOT NULL DEFAULT '',
            metadata    JSONB       NOT NULL DEFAULT '{}',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT lead_execution_events_pkey PRIMARY KEY (event_id),
            CONSTRAINT lead_execution_events_status_check
                CHECK (status IN ('SUCCESS','FAILURE','WARN','INFO')),
            CONSTRAINT lead_execution_events_campaign_id_fkey
                FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
            CONSTRAINT lead_execution_events_tenant_id_fkey
                FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            CONSTRAINT lead_execution_events_lead_id_fkey
                FOREIGN KEY (lead_id) REFERENCES leads(lead_id) ON DELETE CASCADE,
            CONSTRAINT lead_execution_events_pipeline_id_fkey
                FOREIGN KEY (pipeline_id) REFERENCES pipelines(pipeline_id) ON DELETE SET NULL
        )
    """)

    op.execute("""
        CREATE INDEX idx_lead_events_campaign
            ON lead_execution_events (tenant_id, campaign_id, created_at DESC);
        CREATE INDEX idx_lead_events_lead_id
            ON lead_execution_events (lead_id);
        CREATE INDEX idx_lead_events_pipeline
            ON lead_execution_events (pipeline_id, created_at DESC);
    """)

    op.execute("""
        CREATE TABLE campaign_qualification_rules (
            rule_id     UUID        NOT NULL DEFAULT gen_random_uuid(),
            tenant_id   UUID        NOT NULL,
            campaign_id UUID        NOT NULL,
            field       TEXT        NOT NULL,
            operator    TEXT        NOT NULL,
            value       TEXT        NOT NULL DEFAULT '',
            action      TEXT        NOT NULL DEFAULT 'REQUIRE',
            priority    SMALLINT    NOT NULL DEFAULT 0,
            is_active   BOOLEAN     NOT NULL DEFAULT true,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT campaign_qualification_rules_pkey PRIMARY KEY (rule_id),
            CONSTRAINT campaign_qualification_rules_action_check
                CHECK (action IN ('REQUIRE','REJECT','QUALIFY')),
            CONSTRAINT campaign_qualification_rules_campaign_id_fkey
                FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
            CONSTRAINT campaign_qualification_rules_tenant_id_fkey
                FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE
        )
    """)

    op.execute("""
        CREATE INDEX idx_cqr_campaign ON campaign_qualification_rules (tenant_id, campaign_id);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS campaign_qualification_rules CASCADE")
    op.execute("DROP TABLE IF EXISTS lead_execution_events CASCADE")
    op.execute("DROP TABLE IF EXISTS lead_enrichment_log CASCADE")
    op.execute("DROP TABLE IF EXISTS pipeline_distribution_rules CASCADE")
    op.execute("""
        ALTER TABLE pipelines
            DROP CONSTRAINT IF EXISTS pipelines_current_lead_id_fkey
    """)
    op.execute("DROP TABLE IF EXISTS leads CASCADE")
    op.execute("DROP TABLE IF EXISTS lead_imports CASCADE")
    op.execute("DROP TABLE IF EXISTS pipeline_stages CASCADE")
    op.execute("DROP TABLE IF EXISTS pipelines CASCADE")
