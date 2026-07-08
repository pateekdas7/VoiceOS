"""AIConfigAdminController -- prompt versions, model configs administration (V5 Ch13).

Architecture: V5 Ch13 (Administration Portal); V5 Ch14 (AI Configuration Platform).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.libs.contracts.models.ai_config import ModelConfig, PromptVersion
from src.libs.contracts.primitives import TenantId
from src.services.ai_config.prompt_versioning import PromptImmutableError

if TYPE_CHECKING:
    from src.services.ai_config.service import AIConfigService

__all__ = ["AIConfigAdminController", "PromptImmutableError"]


class AIConfigAdminController:
    """Prompt version + model config administration (V5 Ch13/Ch14).

    ``edit_prompt_version`` is what backs the Phase 2 CPU-validation check
    "``PATCH /ai-config/prompt-versions/{id}`` after publish -> 422" --
    :class:`PromptImmutableError` is the exception the AdminAPI route maps
    to HTTP 422.
    """

    def __init__(self, ai_config_service: AIConfigService) -> None:
        self._ai_config = ai_config_service

    def create_prompt_version(
        self, tenant_id: TenantId, name: str, template: str, language: str = "en"
    ) -> PromptVersion:
        return self._ai_config.create_prompt_version(tenant_id, name, template, language)

    def publish_prompt_version(self, tenant_id: TenantId, prompt_version_id: str) -> PromptVersion:
        return self._ai_config.publish_prompt_version(tenant_id, prompt_version_id)

    def edit_prompt_version(self, tenant_id: TenantId, prompt_version_id: str, template: str) -> PromptVersion:
        """Raises :class:`PromptImmutableError` if the version is already PUBLISHED."""
        return self._ai_config.prompt_versioning.edit(tenant_id, prompt_version_id, template)

    def configure_model(self, tenant_id: TenantId, model_config: ModelConfig, campaign_id: str | None = None) -> None:
        self._ai_config.configure_model(tenant_id, model_config, campaign_id)
