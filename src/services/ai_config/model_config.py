"""ModelConfigService -- adapter/model selection resolved per tenant/campaign (V5 Ch14).

Inheritance order (Sprint-025.md): global default -> tenant override ->
campaign override. Resolved configs are cached per-call in Redis (TTLGuard,
short TTL -- a call's model config never needs to be read twice from
Postgres).

Architecture: V5 Ch14 (AI Configuration Platform -- Model Configuration).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from src.libs.contracts.models.ai_config import ModelConfig
from src.libs.contracts.primitives import TenantId
from src.libs.redis_client.ttl_guard import TTLGuard

from . import metrics

KNOWN_STT_ADAPTERS = frozenset({"whisper"})
KNOWN_LLM_ADAPTERS = frozenset({"vllm"})
KNOWN_TTS_ADAPTERS = frozenset({"veena"})
"""The adapter families this VoiceOS deployment ships (CLAUDE.md "AI Model Rules": STT/LLM/TTS
are replaceable adapters, but only from a known, registered set -- see project_model_lock)."""


class InvalidModelConfigError(ValueError):
    """Raised by :meth:`ModelConfigService.configure` when an adapter name is not registered."""


class ModelConfigRepositoryPort(Protocol):
    def upsert(self, config: ModelConfig) -> ModelConfig: ...
    def get_tenant_default(self, tenant_id: TenantId) -> ModelConfig | None: ...
    def get_campaign_override(self, tenant_id: TenantId, campaign_id: str) -> ModelConfig | None: ...


GLOBAL_DEFAULT_MODEL_CONFIG = ModelConfig(
    model_config_id="global-default",
    tenant_id=TenantId("__global__"),
    campaign_id=None,
    created_at=datetime(2026, 1, 1, tzinfo=UTC),
    updated_at=datetime(2026, 1, 1, tzinfo=UTC),
)
"""The lowest tier of the inheritance chain -- matches the GPU node's locked
model selections (STT: Whisper Large-v3 Turbo; LLM: Qwen2.5-7B-Instruct-FP8;
TTS: Veena AI), see ``ModelConfig``'s own field defaults."""

_CACHE_TTL_SECONDS = 300


class ModelConfigService:
    """Configure and resolve per-tenant/per-campaign model configuration (V5 Ch14)."""

    def __init__(self, repository: ModelConfigRepositoryPort, redis: Any | None = None) -> None:
        self._repo = repository
        self._ttl_guard = TTLGuard(redis) if redis is not None else None
        self._redis = redis

    def configure(self, tenant_id: TenantId, model_config: ModelConfig, campaign_id: str | None = None) -> None:
        """Store a tenant-level default (``campaign_id=None``) or a campaign override.

        Raises :class:`InvalidModelConfigError` if any adapter name is not in the
        known/registered set (Sprint-025.md: "configuration validation"). Numeric bounds
        (e.g. ``llm_temperature``) are already enforced by ``ModelConfig``'s own pydantic
        field constraints at construction time.
        """
        self._validate_adapters(model_config)
        now = datetime.now(UTC)
        record = ModelConfig(
            model_config_id=model_config.model_config_id or str(uuid.uuid4()),
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            stt_adapter=model_config.stt_adapter,
            stt_model=model_config.stt_model,
            llm_adapter=model_config.llm_adapter,
            llm_model=model_config.llm_model,
            llm_temperature=model_config.llm_temperature,
            tts_adapter=model_config.tts_adapter,
            tts_voice=model_config.tts_voice,
            created_at=now,
            updated_at=now,
        )
        self._repo.upsert(record)
        self._invalidate_cache(tenant_id, campaign_id)

    def resolve(self, tenant_id: TenantId, campaign_id: str | None = None) -> ModelConfig:
        """Resolve the effective config: global default -> tenant override -> campaign override.

        Cached per (tenant_id, campaign_id) in Redis for ``_CACHE_TTL_SECONDS``
        when a Redis client was supplied; falls back to a direct Postgres
        read (no error) when it wasn't -- same optional-cache precedent as
        every Sprint-016+ circuit breaker/health-check parameter.
        """
        cache_key = self._cache_key(tenant_id, campaign_id)
        if self._redis is not None:
            cached = self._redis.get(cache_key)
            if cached is not None:
                metrics.record_model_config_resolution(cache_hit=True)
                return ModelConfig.model_validate_json(cached)

        effective = GLOBAL_DEFAULT_MODEL_CONFIG
        tenant_default = self._repo.get_tenant_default(tenant_id)
        if tenant_default is not None:
            effective = tenant_default
        if campaign_id is not None:
            campaign_override = self._repo.get_campaign_override(tenant_id, campaign_id)
            if campaign_override is not None:
                effective = campaign_override

        if self._ttl_guard is not None:
            self._ttl_guard.set(cache_key, effective.model_dump_json(), ex=_CACHE_TTL_SECONDS)
        metrics.record_model_config_resolution(cache_hit=False)
        return effective

    @staticmethod
    def _validate_adapters(model_config: ModelConfig) -> None:
        if model_config.stt_adapter not in KNOWN_STT_ADAPTERS:
            raise InvalidModelConfigError(f"unknown stt_adapter {model_config.stt_adapter!r}")
        if model_config.llm_adapter not in KNOWN_LLM_ADAPTERS:
            raise InvalidModelConfigError(f"unknown llm_adapter {model_config.llm_adapter!r}")
        if model_config.tts_adapter not in KNOWN_TTS_ADAPTERS:
            raise InvalidModelConfigError(f"unknown tts_adapter {model_config.tts_adapter!r}")

    def _invalidate_cache(self, tenant_id: TenantId, campaign_id: str | None) -> None:
        if self._redis is None:
            return
        self._redis.delete(self._cache_key(tenant_id, campaign_id))

    @staticmethod
    def _cache_key(tenant_id: TenantId, campaign_id: str | None) -> str:
        return f"voiceos:model_config:{tenant_id}:{campaign_id or 'default'}"


__all__ = [
    "GLOBAL_DEFAULT_MODEL_CONFIG",
    "KNOWN_LLM_ADAPTERS",
    "KNOWN_STT_ADAPTERS",
    "KNOWN_TTS_ADAPTERS",
    "InvalidModelConfigError",
    "ModelConfigRepositoryPort",
    "ModelConfigService",
]
