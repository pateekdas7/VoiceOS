"""PromptVersioningService -- immutable, hash-verified prompt template versions (V5 Ch14).

Prompt templates are versioned per (tenant_id, name). ``create_version``
always starts a new version at DRAFT; ``publish`` freezes it (RI-7 hash
verification uses ``PromptVersion.hash`` from that point on); a published
version can never be edited -- "rollback" is re-pinning a campaign to an
older, already-published version, not mutating history.

Architecture: V5 Ch14 (AI Configuration Platform -- Prompt Versioning);
Invariant RI-7 (prompt determinism).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from src.libs.contracts.models.ai_config import PromptVersion, PromptVersionStatus
from src.libs.contracts.primitives import TenantId

from . import metrics


class PromptVersionRepositoryPort(Protocol):
    def create(self, version: PromptVersion) -> PromptVersion: ...
    def get(self, tenant_id: TenantId, prompt_version_id: str) -> PromptVersion | None: ...
    def latest_version_number(self, tenant_id: TenantId, name: str) -> int: ...
    def list_versions(self, tenant_id: TenantId, name: str) -> tuple[PromptVersion, ...]: ...
    def mark_published(self, tenant_id: TenantId, prompt_version_id: str, published_at: Any) -> int: ...
    def pin(self, tenant_id: TenantId, campaign_id: str, prompt_version_id: str) -> None: ...
    def pinned_version_id(self, tenant_id: TenantId, campaign_id: str) -> str | None: ...


class PromptImmutableError(ValueError):
    """Raised when an attempt is made to modify a PUBLISHED prompt version."""


class PromptVersionNotFoundError(LookupError):
    """Raised when a referenced prompt_version_id does not exist for the tenant."""


class PromptVersioningService:
    """Create, publish, and pin immutable prompt template versions (V5 Ch14)."""

    def __init__(self, repository: PromptVersionRepositoryPort) -> None:
        self._repo = repository

    def create_version(self, tenant_id: TenantId, name: str, template: str, language: str = "en") -> PromptVersion:
        """Create a new DRAFT version, auto-incrementing ``version_number`` for (tenant_id, name)."""
        version_number = self._repo.latest_version_number(tenant_id, name) + 1
        version = PromptVersion(
            prompt_version_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            name=name,
            template=template,
            language=language,
            version_number=version_number,
            hash=self.hash_template(template),
            status=PromptVersionStatus.DRAFT,
            created_by="system",
            created_at=datetime.now(UTC),
        )
        return self._repo.create(version)

    def publish(self, tenant_id: TenantId, prompt_version_id: str) -> PromptVersion:
        """DRAFT -> PUBLISHED. Once published, ``edit`` (and any other mutation) raises."""
        version = self._require(tenant_id, prompt_version_id)
        if version.status == PromptVersionStatus.PUBLISHED:
            raise PromptImmutableError(f"prompt version {prompt_version_id} is already PUBLISHED and immutable")
        published_at = datetime.now(UTC)
        self._repo.mark_published(tenant_id, prompt_version_id, published_at)
        metrics.record_prompt_version_published(str(tenant_id))
        return self._require(tenant_id, prompt_version_id)

    def edit(self, tenant_id: TenantId, prompt_version_id: str, template: str) -> PromptVersion:
        """Edit a DRAFT version's template. Raises :class:`PromptImmutableError` once PUBLISHED."""
        version = self._require(tenant_id, prompt_version_id)
        if version.status == PromptVersionStatus.PUBLISHED:
            raise PromptImmutableError(
                f"prompt version {prompt_version_id} is PUBLISHED and immutable -- create a new version instead"
            )
        # Sprint-025 scope: DRAFT edits are represented as a fresh version
        # row (append-only), never an in-place UPDATE of `template`/`hash` --
        # this keeps every prompt_versions row's hash permanently trustworthy.
        return self.create_version(tenant_id, version.name, template, version.language)

    def pin(self, tenant_id: TenantId, campaign_id: str, prompt_version_id: str) -> None:
        """Pin a campaign to an exact prompt version (also how "rollback" is expressed)."""
        version = self._require(tenant_id, prompt_version_id)
        if version.status != PromptVersionStatus.PUBLISHED:
            raise PromptImmutableError(f"cannot pin campaign to a non-PUBLISHED prompt version ({prompt_version_id})")
        self._repo.pin(tenant_id, campaign_id, prompt_version_id)

    def pinned_version(self, tenant_id: TenantId, campaign_id: str) -> PromptVersion | None:
        version_id = self._repo.pinned_version_id(tenant_id, campaign_id)
        return self._repo.get(tenant_id, version_id) if version_id is not None else None

    def get(self, tenant_id: TenantId, prompt_version_id: str) -> PromptVersion | None:
        return self._repo.get(tenant_id, prompt_version_id)

    def list_versions(self, tenant_id: TenantId, name: str) -> tuple[PromptVersion, ...]:
        return self._repo.list_versions(tenant_id, name)

    def _require(self, tenant_id: TenantId, prompt_version_id: str) -> PromptVersion:
        version = self._repo.get(tenant_id, prompt_version_id)
        if version is None:
            raise PromptVersionNotFoundError(f"prompt version not found: {prompt_version_id}")
        return version

    @staticmethod
    def hash_template(template: str) -> str:
        """SHA-256 hex digest of ``template`` -- matches RI-7's ``PromptContract.hash_prompt``."""
        return hashlib.sha256(template.encode("utf-8")).hexdigest()


__all__ = [
    "PromptImmutableError",
    "PromptVersionNotFoundError",
    "PromptVersionRepositoryPort",
    "PromptVersioningService",
]
