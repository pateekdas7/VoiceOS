"""Unit tests for the AI Configuration Platform (Sprint-025, V5 Ch14).

All tests run fully in-process -- no live Postgres/Redis required (Phase 1).
Repository interactions use small in-memory fake doubles, mirroring the
``_Fake*Repository`` precedent from ``test_billing.py``/``test_crm.py``.

Required named tests (Sprint-025.md):
    test_prompt_version_immutable_after_publish -- publish -> edit -> raises
    test_prompt_version_hash_matches -- version hash = sha256(template)
    test_model_config_inheritance -- campaign config overrides tenant default
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from src.libs.contracts.models.ai_config import ModelConfig, PromptVersion, PromptVersionStatus
from src.libs.contracts.primitives import TenantId
from src.services.ai_config.eval_runner import EvaluationRunService
from src.services.ai_config.model_config import GLOBAL_DEFAULT_MODEL_CONFIG, InvalidModelConfigError, ModelConfigService
from src.services.ai_config.prompt_versioning import PromptImmutableError, PromptVersioningService

_TENANT = TenantId("tenant-a")
_OTHER_TENANT = TenantId("tenant-b")


class _FakePromptVersionRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, PromptVersion] = {}
        self._pins: dict[str, str] = {}

    def create(self, version: PromptVersion) -> PromptVersion:
        self._by_id[version.prompt_version_id] = version
        return version

    def get(self, tenant_id: TenantId, prompt_version_id: str) -> PromptVersion | None:
        version = self._by_id.get(prompt_version_id)
        return version if version is not None and version.tenant_id == tenant_id else None

    def latest_version_number(self, tenant_id: TenantId, name: str) -> int:
        matches = [v.version_number for v in self._by_id.values() if v.tenant_id == tenant_id and v.name == name]
        return max(matches) if matches else 0

    def list_versions(self, tenant_id: TenantId, name: str) -> tuple[PromptVersion, ...]:
        return tuple(
            sorted(
                (v for v in self._by_id.values() if v.tenant_id == tenant_id and v.name == name),
                key=lambda v: v.version_number,
                reverse=True,
            )
        )

    def mark_published(self, tenant_id: TenantId, prompt_version_id: str, published_at: object) -> int:
        version = self._by_id[prompt_version_id]
        self._by_id[prompt_version_id] = version.model_copy(
            update={"status": PromptVersionStatus.PUBLISHED, "published_at": published_at}
        )
        return 1

    def pin(self, tenant_id: TenantId, campaign_id: str, prompt_version_id: str) -> None:
        self._pins[campaign_id] = prompt_version_id

    def pinned_version_id(self, tenant_id: TenantId, campaign_id: str) -> str | None:
        return self._pins.get(campaign_id)


class _FakeModelConfigRepository:
    def __init__(self) -> None:
        self._defaults: dict[TenantId, ModelConfig] = {}
        self._overrides: dict[tuple[TenantId, str], ModelConfig] = {}

    def upsert(self, config: ModelConfig) -> ModelConfig:
        if config.campaign_id is None:
            self._defaults[config.tenant_id] = config
        else:
            self._overrides[(config.tenant_id, config.campaign_id)] = config
        return config

    def get_tenant_default(self, tenant_id: TenantId) -> ModelConfig | None:
        return self._defaults.get(tenant_id)

    def get_campaign_override(self, tenant_id: TenantId, campaign_id: str) -> ModelConfig | None:
        return self._overrides.get((tenant_id, campaign_id))


class TestPromptVersioningService:
    def test_prompt_version_hash_matches(self) -> None:
        repo = _FakePromptVersionRepository()
        service = PromptVersioningService(repo)
        template = "Negotiate a payment with the customer."

        version = service.create_version(_TENANT, "collections_negotiation", template)

        assert version.hash == hashlib.sha256(template.encode("utf-8")).hexdigest()
        assert version.status == PromptVersionStatus.DRAFT
        assert version.version_number == 1

    def test_prompt_version_immutable_after_publish(self) -> None:
        repo = _FakePromptVersionRepository()
        service = PromptVersioningService(repo)
        version = service.create_version(_TENANT, "collections_negotiation", "v1 template")

        published = service.publish(_TENANT, version.prompt_version_id)
        assert published.status == PromptVersionStatus.PUBLISHED
        assert published.published_at is not None

        with pytest.raises(PromptImmutableError):
            service.edit(_TENANT, version.prompt_version_id, "an edited template")

        with pytest.raises(PromptImmutableError):
            service.publish(_TENANT, version.prompt_version_id)

    def test_edit_before_publish_creates_new_draft_version(self) -> None:
        repo = _FakePromptVersionRepository()
        service = PromptVersioningService(repo)
        v1 = service.create_version(_TENANT, "collections_negotiation", "v1 template")

        v2 = service.edit(_TENANT, v1.prompt_version_id, "v2 template")

        assert v2.version_number == 2
        assert v2.template == "v2 template"
        assert v2.status == PromptVersionStatus.DRAFT

    def test_pin_requires_published_version(self) -> None:
        repo = _FakePromptVersionRepository()
        service = PromptVersioningService(repo)
        version = service.create_version(_TENANT, "collections_negotiation", "v1 template")

        with pytest.raises(PromptImmutableError):
            service.pin(_TENANT, "campaign-1", version.prompt_version_id)

        service.publish(_TENANT, version.prompt_version_id)
        service.pin(_TENANT, "campaign-1", version.prompt_version_id)
        pinned = service.pinned_version(_TENANT, "campaign-1")
        assert pinned is not None
        assert pinned.prompt_version_id == version.prompt_version_id


class TestEvaluationRunService:
    def test_run_scores_against_canned_cases(self) -> None:
        version = PromptVersion(
            prompt_version_id="v1",
            tenant_id=_TENANT,
            name="collections_negotiation",
            template="Negotiate the payment amount with the customer.",
            version_number=1,
            hash="deadbeef",
            created_by="system",
            created_at=datetime.now(UTC),
        )
        result = EvaluationRunService().run(version)
        assert result.case_count == 3
        assert result.passed_count == 3  # template contains "negotiate", "payment", and "customer"
        assert result.score == 1.0


class TestModelConfigService:
    def test_model_config_inheritance(self) -> None:
        repo = _FakeModelConfigRepository()
        service = ModelConfigService(repo)

        # No config configured yet -> global default.
        resolved = service.resolve(_TENANT, campaign_id="campaign-1")
        assert resolved.llm_model == GLOBAL_DEFAULT_MODEL_CONFIG.llm_model

        # Tenant default overrides global default.
        tenant_default = ModelConfig(
            model_config_id="",
            tenant_id=_TENANT,
            llm_temperature=0.5,
            created_at=resolved.created_at,
            updated_at=resolved.updated_at,
        )
        service.configure(_TENANT, tenant_default)
        resolved = service.resolve(_TENANT, campaign_id="campaign-1")
        assert resolved.llm_temperature == 0.5

        # Campaign override wins over tenant default (Sprint-025.md AC).
        campaign_override = ModelConfig(
            model_config_id="",
            tenant_id=_TENANT,
            llm_temperature=0.9,
            created_at=resolved.created_at,
            updated_at=resolved.updated_at,
        )
        service.configure(_TENANT, campaign_override, campaign_id="campaign-1")
        resolved = service.resolve(_TENANT, campaign_id="campaign-1")
        assert resolved.llm_temperature == 0.9

        # A different campaign with no override still sees the tenant default.
        resolved_other = service.resolve(_TENANT, campaign_id="campaign-2")
        assert resolved_other.llm_temperature == 0.5

    def test_tenant_isolation(self) -> None:
        repo = _FakeModelConfigRepository()
        service = ModelConfigService(repo)
        service.configure(_TENANT, ModelConfig(model_config_id="", tenant_id=_TENANT, llm_temperature=0.9))

        resolved = service.resolve(_OTHER_TENANT)
        assert resolved.llm_temperature == GLOBAL_DEFAULT_MODEL_CONFIG.llm_temperature

    def test_configure_rejects_unknown_adapter(self) -> None:
        repo = _FakeModelConfigRepository()
        service = ModelConfigService(repo)

        with pytest.raises(InvalidModelConfigError):
            service.configure(_TENANT, ModelConfig(model_config_id="", tenant_id=_TENANT, stt_adapter="deepgram"))

        with pytest.raises(InvalidModelConfigError):
            service.configure(_TENANT, ModelConfig(model_config_id="", tenant_id=_TENANT, llm_adapter="openai"))

        with pytest.raises(InvalidModelConfigError):
            service.configure(_TENANT, ModelConfig(model_config_id="", tenant_id=_TENANT, tts_adapter="elevenlabs"))
