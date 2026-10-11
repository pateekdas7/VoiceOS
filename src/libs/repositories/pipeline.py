"""PipelineRepository — tenant-scoped CRUD for campaign pipeline records.

Architecture: V5 Ch6 (Campaign Engine — Lead Distribution, ADR-005 §14).
"""

from __future__ import annotations

from typing import Any

from ..contracts.models.pipeline import Pipeline, PipelineStatus
from ..contracts.primitives import CampaignId, PipelineId, TenantId
from .base import BaseRepository

_TABLE = "pipelines"

_COLUMNS = (
    "pipeline_id",
    "tenant_id",
    "campaign_id",
    "name",
    "status",
    "created_at",
    "updated_at",
    "created_by",
)


class PipelineRepository(BaseRepository):
    """Tenant-scoped CRUD for pipeline records."""

    def create(self, pipeline: Pipeline) -> Pipeline:
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                pipeline_id, tenant_id, campaign_id, name, status,
                created_at, updated_at, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                pipeline.pipeline_id,
                pipeline.tenant_id,
                pipeline.campaign_id,
                pipeline.name,
                pipeline.status.value,
                pipeline.created_at,
                pipeline.updated_at,
                pipeline.created_by,
            ),
        )
        self._commit()
        return pipeline

    def get(self, tenant_id: TenantId, pipeline_id: PipelineId) -> Pipeline | None:
        row = self._tenant_select_one(
            _TABLE,
            _COLUMNS,
            tenant_id,
            extra_where="pipeline_id = %s",
            extra_params=(pipeline_id,),
        )
        return self._hydrate(row) if row is not None else None

    def find_by_campaign(self, tenant_id: TenantId, campaign_id: CampaignId) -> tuple[Pipeline, ...]:
        rows = self._tenant_select(
            _TABLE,
            _COLUMNS,
            tenant_id,
            extra_where="campaign_id = %s",
            extra_params=(campaign_id,),
            order_by="created_at ASC",
        )
        return tuple(self._hydrate(row) for row in rows)

    def find_active_for_campaign(self, tenant_id: TenantId, campaign_id: CampaignId) -> tuple[Pipeline, ...]:
        rows = self._tenant_select(
            _TABLE,
            _COLUMNS,
            tenant_id,
            extra_where="campaign_id = %s AND status = 'ACTIVE'",
            extra_params=(campaign_id,),
            order_by="created_at ASC",
        )
        return tuple(self._hydrate(row) for row in rows)

    def update_status(self, tenant_id: TenantId, pipeline_id: PipelineId, status: PipelineStatus) -> None:
        from datetime import UTC, datetime

        self._tenant_update(
            _TABLE,
            ("status", "updated_at"),
            (status.value, datetime.now(UTC)),
            tenant_id,
            extra_where="pipeline_id = %s",
            extra_params=(pipeline_id,),
        )

    def update_name(self, tenant_id: TenantId, pipeline_id: PipelineId, name: str) -> None:
        from datetime import UTC, datetime

        self._tenant_update(
            _TABLE,
            ("name", "updated_at"),
            (name, datetime.now(UTC)),
            tenant_id,
            extra_where="pipeline_id = %s",
            extra_params=(pipeline_id,),
        )

    def _hydrate(self, row: tuple[Any, ...]) -> Pipeline:
        (pipeline_id, tenant_id, campaign_id, name, status, created_at, updated_at, created_by) = row
        return Pipeline(
            pipeline_id=PipelineId(str(pipeline_id)),
            tenant_id=TenantId(str(tenant_id)),
            campaign_id=CampaignId(str(campaign_id)),
            name=name,
            status=PipelineStatus(status),
            created_at=created_at,
            updated_at=updated_at,
            created_by=created_by,
        )
