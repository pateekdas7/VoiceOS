"""Persistent data models for the AI Configuration Platform.

Prompt templates are versioned and immutable once published; model
configuration (adapter/model selection + inference params) is resolved
through a global-default -> tenant-override -> campaign-override chain.

Architecture: V5 Ch14 (AI Configuration Platform).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..primitives import TenantId


class PromptVersionStatus(StrEnum):
    """Lifecycle status of a prompt version (V5 Ch14 -- 2-state, no in-between)."""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class PromptVersion(BaseModel):
    """One immutable-once-published version of a named prompt template (V5 Ch14).

    ``hash`` is the SHA-256 hex digest of ``template`` at creation time --
    RI-7 validation compares a live prompt's hash against this stored value.
    """

    model_config = ConfigDict(frozen=True)

    prompt_version_id: str
    tenant_id: TenantId
    name: str
    """Logical prompt name (e.g. 'collections_negotiation_v1'). Multiple versions share a name."""
    template: str
    language: str = "en"
    version_number: int = Field(ge=1)
    """Monotonically increasing per (tenant_id, name) -- 1 for the first version."""
    hash: str
    """SHA-256 hex digest of ``template`` (64 lowercase hex chars)."""
    status: PromptVersionStatus = PromptVersionStatus.DRAFT
    created_by: str
    created_at: datetime
    published_at: datetime | None = None


class ModelConfig(BaseModel):
    """Adapter selection + inference parameters, resolved per tenant/campaign (V5 Ch14).

    A row with ``campaign_id is None`` is the tenant-level default; a row
    with ``campaign_id`` set is a campaign override. Inheritance order:
    global default (module constant) -> tenant override -> campaign override.
    """

    model_config = ConfigDict(frozen=True)

    model_config_id: str
    tenant_id: TenantId
    campaign_id: str | None = None
    stt_adapter: str = "whisper"
    stt_model: str = "whisper-large-v3-turbo"
    llm_adapter: str = "vllm"
    llm_model: str = "qwen2.5-7b-instruct-fp8"
    llm_temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    tts_adapter: str = "veena"
    tts_voice: str = "kavya"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EvalRunStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class EvalRunResult(BaseModel):
    """Result of triggering an evaluation run for a published prompt version (V5 Ch14.6).

    Sprint-025 scope: a deterministic keyword-match scorer over a fixed
    canned case set (no live GPU/LLM call) -- a full model-graded evaluation
    harness is documented future work, same "documented proxy" precedent as
    Sprint-024's ForecastingEngine.
    """

    model_config = ConfigDict(frozen=True)

    eval_run_id: str
    prompt_version_id: str
    status: EvalRunStatus
    case_count: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    score: float = Field(ge=0.0, le=1.0)
    run_at: datetime


__all__ = [
    "EvalRunResult",
    "EvalRunStatus",
    "ModelConfig",
    "PromptVersion",
    "PromptVersionStatus",
]
