"""PromptVersionRepository, ModelConfigRepository -- AI Configuration Platform (V5 Ch14).

Architecture: V5 Ch14 (AI Configuration Platform).
"""

from __future__ import annotations

from typing import Any

from ..contracts.models.ai_config import ModelConfig, PromptVersion, PromptVersionStatus
from ..contracts.primitives import TenantId
from .base import BaseRepository

_PROMPT_VERSIONS_TABLE = "prompt_versions"
_MODEL_CONFIGS_TABLE = "model_configs"
_PROMPT_PINS_TABLE = "campaign_prompt_pins"

_PROMPT_VERSION_COLUMNS = (
    "prompt_version_id",
    "tenant_id",
    "name",
    "template",
    "language",
    "version_number",
    "hash",
    "status",
    "created_by",
    "created_at",
    "published_at",
)

_MODEL_CONFIG_COLUMNS = (
    "model_config_id",
    "tenant_id",
    "campaign_id",
    "stt_adapter",
    "stt_model",
    "llm_adapter",
    "llm_model",
    "llm_temperature",
    "tts_adapter",
    "tts_voice",
    "created_at",
    "updated_at",
)


class PromptVersionRepository(BaseRepository):
    """Tenant-scoped queries for the ``prompt_versions``/``campaign_prompt_pins`` domain."""

    def create(self, version: PromptVersion) -> PromptVersion:
        self._execute(
            f"""
            INSERT INTO {_PROMPT_VERSIONS_TABLE} (
                prompt_version_id, tenant_id, name, template, language, version_number,
                hash, status, created_by, created_at, published_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                version.prompt_version_id,
                version.tenant_id,
                version.name,
                version.template,
                version.language,
                version.version_number,
                version.hash,
                version.status.value,
                version.created_by,
                version.created_at,
                version.published_at,
            ),
        )
        self._commit()
        return version

    def get(self, tenant_id: TenantId, prompt_version_id: str) -> PromptVersion | None:
        row = self._tenant_select_one(
            _PROMPT_VERSIONS_TABLE,
            _PROMPT_VERSION_COLUMNS,
            tenant_id,
            extra_where="prompt_version_id = %s",
            extra_params=(prompt_version_id,),
        )
        return self._hydrate(row) if row is not None else None

    def latest_version_number(self, tenant_id: TenantId, name: str) -> int:
        rows = self._tenant_select(
            _PROMPT_VERSIONS_TABLE,
            ("version_number",),
            tenant_id,
            extra_where="name = %s",
            extra_params=(name,),
            order_by="version_number DESC",
            limit=1,
        )
        return int(rows[0][0]) if rows else 0

    def list_versions(self, tenant_id: TenantId, name: str) -> tuple[PromptVersion, ...]:
        rows = self._tenant_select(
            _PROMPT_VERSIONS_TABLE,
            _PROMPT_VERSION_COLUMNS,
            tenant_id,
            extra_where="name = %s",
            extra_params=(name,),
            order_by="version_number DESC",
        )
        return tuple(self._hydrate(row) for row in rows)

    def mark_published(self, tenant_id: TenantId, prompt_version_id: str, published_at: Any) -> int:
        return self._tenant_update(
            _PROMPT_VERSIONS_TABLE,
            ("status", "published_at"),
            (PromptVersionStatus.PUBLISHED.value, published_at),
            tenant_id,
            extra_where="prompt_version_id = %s",
            extra_params=(prompt_version_id,),
        )

    def pin(self, tenant_id: TenantId, campaign_id: str, prompt_version_id: str) -> None:
        self._execute(
            f"""
            INSERT INTO {_PROMPT_PINS_TABLE} (campaign_id, tenant_id, prompt_version_id, pinned_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (campaign_id) DO UPDATE SET prompt_version_id = EXCLUDED.prompt_version_id,
                                                     pinned_at = NOW()
            """,
            (campaign_id, tenant_id, prompt_version_id),
        )
        self._commit()

    def pinned_version_id(self, tenant_id: TenantId, campaign_id: str) -> str | None:
        rows = self._tenant_select(
            _PROMPT_PINS_TABLE,
            ("prompt_version_id",),
            tenant_id,
            extra_where="campaign_id = %s",
            extra_params=(campaign_id,),
        )
        return str(rows[0][0]) if rows else None

    def _hydrate(self, row: tuple[Any, ...]) -> PromptVersion:
        (
            prompt_version_id,
            tenant_id,
            name,
            template,
            language,
            version_number,
            hash_,
            status,
            created_by,
            created_at,
            published_at,
        ) = row
        return PromptVersion(
            prompt_version_id=str(prompt_version_id),
            tenant_id=TenantId(tenant_id),
            name=name,
            template=template,
            language=language,
            version_number=version_number,
            hash=hash_,
            status=PromptVersionStatus(status),
            created_by=created_by,
            created_at=created_at,
            published_at=published_at,
        )


class ModelConfigRepository(BaseRepository):
    """Tenant-scoped queries for the ``model_configs`` domain.

    A row with ``campaign_id IS NULL`` is the tenant-level default; a row
    with ``campaign_id`` set is a campaign override (mutually-exclusive
    partial-unique-index-enforced, migration 0024).
    """

    def upsert(self, config: ModelConfig) -> ModelConfig:
        # Migration 0024's uq_model_configs_tenant_default/_campaign are partial
        # UNIQUE INDEXes, not named CONSTRAINTs -- `ON CONFLICT ON CONSTRAINT`
        # only resolves actual constraints (Postgres real-infra finding, this
        # sprint). A partial index is targeted by repeating its own (columns)
        # + WHERE predicate, which Postgres then matches by inference.
        conflict_target = (
            "(tenant_id) WHERE campaign_id IS NULL"
            if config.campaign_id is None
            else "(tenant_id, campaign_id) WHERE campaign_id IS NOT NULL"
        )
        self._execute(
            f"""
            INSERT INTO {_MODEL_CONFIGS_TABLE} (
                model_config_id, tenant_id, campaign_id, stt_adapter, stt_model,
                llm_adapter, llm_model, llm_temperature, tts_adapter, tts_voice,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT {conflict_target} DO UPDATE SET
                stt_adapter = EXCLUDED.stt_adapter,
                stt_model = EXCLUDED.stt_model,
                llm_adapter = EXCLUDED.llm_adapter,
                llm_model = EXCLUDED.llm_model,
                llm_temperature = EXCLUDED.llm_temperature,
                tts_adapter = EXCLUDED.tts_adapter,
                tts_voice = EXCLUDED.tts_voice,
                updated_at = EXCLUDED.updated_at
            """,
            (
                config.model_config_id,
                config.tenant_id,
                config.campaign_id,
                config.stt_adapter,
                config.stt_model,
                config.llm_adapter,
                config.llm_model,
                config.llm_temperature,
                config.tts_adapter,
                config.tts_voice,
                config.created_at,
                config.updated_at,
            ),
        )
        self._commit()
        return config

    def get_tenant_default(self, tenant_id: TenantId) -> ModelConfig | None:
        row = self._tenant_select_one(
            _MODEL_CONFIGS_TABLE,
            _MODEL_CONFIG_COLUMNS,
            tenant_id,
            extra_where="campaign_id IS NULL",
        )
        return self._hydrate(row) if row is not None else None

    def get_campaign_override(self, tenant_id: TenantId, campaign_id: str) -> ModelConfig | None:
        row = self._tenant_select_one(
            _MODEL_CONFIGS_TABLE,
            _MODEL_CONFIG_COLUMNS,
            tenant_id,
            extra_where="campaign_id = %s",
            extra_params=(campaign_id,),
        )
        return self._hydrate(row) if row is not None else None

    def _hydrate(self, row: tuple[Any, ...]) -> ModelConfig:
        (
            model_config_id,
            tenant_id,
            campaign_id,
            stt_adapter,
            stt_model,
            llm_adapter,
            llm_model,
            llm_temperature,
            tts_adapter,
            tts_voice,
            created_at,
            updated_at,
        ) = row
        return ModelConfig(
            model_config_id=str(model_config_id),
            tenant_id=TenantId(tenant_id),
            campaign_id=str(campaign_id) if campaign_id else None,
            stt_adapter=stt_adapter,
            stt_model=stt_model,
            llm_adapter=llm_adapter,
            llm_model=llm_model,
            llm_temperature=llm_temperature,
            tts_adapter=tts_adapter,
            tts_voice=tts_voice,
            created_at=created_at,
            updated_at=updated_at,
        )


__all__ = ["ModelConfigRepository", "PromptVersionRepository"]
