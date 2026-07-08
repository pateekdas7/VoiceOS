"""AIConfigService -- façade over prompt versioning, model configuration, and eval runs (V5 Ch14).

Library-class facade, no standalone HTTP listener until Sprint-026 (same
precedent as every service since Sprint-013 -- see CPU_NODE_STATE.md §8.1).

Architecture: V5 Ch14 (AI Configuration Platform).
"""

from __future__ import annotations

from src.libs.contracts.models.ai_config import EvalRunResult, ModelConfig, PromptVersion
from src.libs.contracts.primitives import TenantId

from .eval_runner import EvaluationRunService
from .model_config import ModelConfigService
from .prompt_versioning import PromptVersioningService


class AIConfigService:
    """Façade over :class:`PromptVersioningService`, :class:`ModelConfigService`, and eval runs."""

    def __init__(
        self,
        prompt_versioning: PromptVersioningService,
        model_config: ModelConfigService,
        eval_runner: EvaluationRunService | None = None,
    ) -> None:
        self._prompt_versioning = prompt_versioning
        self._model_config = model_config
        self._eval_runner = eval_runner or EvaluationRunService()

    @property
    def prompt_versioning(self) -> PromptVersioningService:
        return self._prompt_versioning

    @property
    def model_config(self) -> ModelConfigService:
        return self._model_config

    def create_prompt_version(
        self, tenant_id: TenantId, name: str, template: str, language: str = "en"
    ) -> PromptVersion:
        return self._prompt_versioning.create_version(tenant_id, name, template, language)

    def publish_prompt_version(self, tenant_id: TenantId, prompt_version_id: str) -> PromptVersion:
        return self._prompt_versioning.publish(tenant_id, prompt_version_id)

    def trigger_eval_run(self, tenant_id: TenantId, prompt_version_id: str) -> EvalRunResult:
        version = self._prompt_versioning.get(tenant_id, prompt_version_id)
        if version is None:
            raise LookupError(f"prompt version not found: {prompt_version_id}")
        return self._eval_runner.run(version)

    def configure_model(self, tenant_id: TenantId, model_config: ModelConfig, campaign_id: str | None = None) -> None:
        self._model_config.configure(tenant_id, model_config, campaign_id)

    def resolve_model_config(self, tenant_id: TenantId, campaign_id: str | None = None) -> ModelConfig:
        return self._model_config.resolve(tenant_id, campaign_id)


__all__ = ["AIConfigService"]
