"""PipelineService — CRUD + lifecycle for campaign pipelines (V5 Ch6, ADR-005 §14)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.libs.audit.logger import AuditLogger
from src.libs.contracts.models.pipeline import Pipeline, PipelineStatus
from src.libs.contracts.primitives import CampaignId, PipelineId, TenantId
from src.libs.event_bus.publisher import Publisher

from . import metrics as _metrics

if TYPE_CHECKING:
    from src.libs.repositories.pipeline import PipelineRepository


class PipelineNotFoundError(ValueError):
    """Raised when a pipeline cannot be found for the given tenant+pipeline_id."""


class InvalidPipelineTransitionError(ValueError):
    """Raised when a pipeline status transition is not permitted."""


_ALLOWED_TRANSITIONS: dict[PipelineStatus, frozenset[PipelineStatus]] = {
    PipelineStatus.DRAFT: frozenset({PipelineStatus.ACTIVE, PipelineStatus.ARCHIVED}),
    PipelineStatus.ACTIVE: frozenset({PipelineStatus.PAUSED, PipelineStatus.ARCHIVED}),
    PipelineStatus.PAUSED: frozenset({PipelineStatus.ACTIVE, PipelineStatus.ARCHIVED}),
    PipelineStatus.ARCHIVED: frozenset(),
}


class PipelineService:
    """CRUD + lifecycle for campaign work-queue pipelines (V5 Ch6, ADR-005 §14)."""

    def __init__(
        self,
        repository: PipelineRepository,
        publisher: Publisher | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._repo = repository
        self._publisher = publisher
        self._audit_logger = audit_logger

    def create(
        self,
        tenant_id: TenantId,
        campaign_id: CampaignId,
        name: str,
        created_by: str,
    ) -> Pipeline:
        now = datetime.now(UTC)
        pipeline = Pipeline(
            pipeline_id=PipelineId(str(uuid.uuid4())),
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            name=name,
            status=PipelineStatus.DRAFT,
            created_at=now,
            updated_at=now,
            created_by=created_by,
        )
        created = self._repo.create(pipeline)
        _metrics.record_pipeline_created(str(tenant_id))
        if self._publisher is not None:
            self._publisher.publish(
                event_type="saas.pipeline.created",
                tenant_id=tenant_id,
                payload={"pipeline_id": created.pipeline_id, "campaign_id": campaign_id, "name": name},
                correlation_id=campaign_id,
            )
        return created

    def get(self, tenant_id: TenantId, pipeline_id: PipelineId) -> Pipeline | None:
        return self._repo.get(tenant_id, pipeline_id)

    def list_for_campaign(self, tenant_id: TenantId, campaign_id: CampaignId) -> tuple[Pipeline, ...]:
        return self._repo.find_by_campaign(tenant_id, campaign_id)

    def activate(self, tenant_id: TenantId, pipeline_id: PipelineId, actor_id: str) -> Pipeline:
        return self._transition(tenant_id, pipeline_id, PipelineStatus.ACTIVE, actor_id=actor_id)

    def pause(self, tenant_id: TenantId, pipeline_id: PipelineId, actor_id: str) -> Pipeline:
        return self._transition(tenant_id, pipeline_id, PipelineStatus.PAUSED, actor_id=actor_id)

    def archive(self, tenant_id: TenantId, pipeline_id: PipelineId, actor_id: str) -> Pipeline:
        return self._transition(tenant_id, pipeline_id, PipelineStatus.ARCHIVED, actor_id=actor_id)

    def rename(self, tenant_id: TenantId, pipeline_id: PipelineId, name: str) -> Pipeline:
        pipeline = self._require(tenant_id, pipeline_id)
        if pipeline.status == PipelineStatus.ARCHIVED:
            raise InvalidPipelineTransitionError("cannot rename an archived pipeline")
        self._repo.update_name(tenant_id, pipeline_id, name)
        return self._require(tenant_id, pipeline_id)

    def _transition(
        self, tenant_id: TenantId, pipeline_id: PipelineId, target: PipelineStatus, *, actor_id: str
    ) -> Pipeline:
        pipeline = self._require(tenant_id, pipeline_id)
        allowed = _ALLOWED_TRANSITIONS.get(pipeline.status, frozenset())
        if target not in allowed:
            raise InvalidPipelineTransitionError(
                f"pipeline {pipeline_id}: {pipeline.status} → {target} is not a valid transition"
            )
        self._repo.update_status(tenant_id, pipeline_id, target)
        if self._audit_logger is not None:
            self._audit_logger.record(
                tenant_id,
                actor_id,
                "pipeline.lifecycle_transition",
                "Pipeline",
                pipeline_id,
                "SUCCESS",
                event_payload={"from": pipeline.status.value, "to": target.value},
            )
        return self._require(tenant_id, pipeline_id)

    def _require(self, tenant_id: TenantId, pipeline_id: PipelineId) -> Pipeline:
        pipeline = self._repo.get(tenant_id, pipeline_id)
        if pipeline is None:
            raise PipelineNotFoundError(f"pipeline not found: {pipeline_id}")
        return pipeline
