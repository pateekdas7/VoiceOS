-- Migration 020: pipeline-level model config
-- Adds pipeline_id FK to model_configs so each pipeline can override
-- the campaign/tenant default STT/LLM/TTS selection.
-- Rollback: ALTER TABLE model_configs DROP COLUMN IF EXISTS pipeline_id;

BEGIN;

ALTER TABLE model_configs
  ADD COLUMN IF NOT EXISTS pipeline_id UUID
    REFERENCES pipelines(pipeline_id) ON DELETE CASCADE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_model_configs_pipeline
  ON model_configs (tenant_id, pipeline_id)
  WHERE pipeline_id IS NOT NULL;

GRANT ALL PRIVILEGES ON TABLE model_configs TO voiceos;

COMMIT;
