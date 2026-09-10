"""Unit tests for PipelineService (CRUD + lifecycle).

Architecture: V5 Ch6 (Campaign Engine — Lead Distribution, ADR-005 §14).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from src.libs.contracts.models.pipeline import Pipeline, PipelineStatus
from src.libs.contracts.primitives import CampaignId, PipelineId, TenantId
from src.services.campaign_management.pipeline_service import (
    InvalidPipelineTransitionError,
    PipelineNotFoundError,
    PipelineService,
)

TENANT = TenantId("t-001")
CAMPAIGN = CampaignId("c-001")
NOW = datetime.now(UTC)


# ─────────────────────────────────────────────────────────────────────────────
# Test double
# ─────────────────────────────────────────────────────────────────────────────

class _FakePipelineRepository:
    def __init__(self) -> None:
        self._store: dict[str, Pipeline] = {}

    def create(self, pipeline: Pipeline) -> Pipeline:
        self._store[pipeline.pipeline_id] = pipeline
        return pipeline

    def get(self, tenant_id: TenantId, pipeline_id: PipelineId) -> Pipeline | None:
        p = self._store.get(pipeline_id)
        if p is None or p.tenant_id != tenant_id:
            return None
        return p

    def find_by_campaign(self, tenant_id: TenantId, campaign_id: CampaignId) -> tuple[Pipeline, ...]:
        return tuple(
            p for p in self._store.values()
            if p.tenant_id == tenant_id and p.campaign_id == campaign_id
        )

    def update_status(self, tenant_id: TenantId, pipeline_id: PipelineId, status: PipelineStatus) -> None:
        p = self._store.get(pipeline_id)
        if p is not None:
            self._store[pipeline_id] = p.model_copy(update={"status": status, "updated_at": datetime.now(UTC)})

    def update_name(self, tenant_id: TenantId, pipeline_id: PipelineId, name: str) -> None:
        p = self._store.get(pipeline_id)
        if p is not None:
            self._store[pipeline_id] = p.model_copy(update={"name": name, "updated_at": datetime.now(UTC)})


def _svc(repo: _FakePipelineRepository | None = None) -> PipelineService:
    return PipelineService(repo or _FakePipelineRepository())


# ─────────────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineServiceCreate:
    def test_creates_in_draft(self) -> None:
        svc = _svc()
        p = svc.create(TENANT, CAMPAIGN, "Pipeline A", created_by="admin")
        assert p.status == PipelineStatus.DRAFT
        assert p.name == "Pipeline A"
        assert p.tenant_id == TENANT
        assert p.campaign_id == CAMPAIGN

    def test_pipeline_id_is_uuid(self) -> None:
        svc = _svc()
        p = svc.create(TENANT, CAMPAIGN, "Pipeline B", created_by="admin")
        uuid.UUID(p.pipeline_id)  # raises ValueError if not a valid UUID

    def test_get_returns_created(self) -> None:
        repo = _FakePipelineRepository()
        svc = _svc(repo)
        p = svc.create(TENANT, CAMPAIGN, "Pipeline C", created_by="admin")
        fetched = svc.get(TENANT, PipelineId(p.pipeline_id))
        assert fetched is not None
        assert fetched.pipeline_id == p.pipeline_id

    def test_get_wrong_tenant_returns_none(self) -> None:
        repo = _FakePipelineRepository()
        svc = _svc(repo)
        p = svc.create(TENANT, CAMPAIGN, "P", created_by="admin")
        other_tenant = TenantId("t-other")
        assert svc.get(other_tenant, PipelineId(p.pipeline_id)) is None

    def test_list_for_campaign(self) -> None:
        repo = _FakePipelineRepository()
        svc = _svc(repo)
        svc.create(TENANT, CAMPAIGN, "A", created_by="admin")
        svc.create(TENANT, CAMPAIGN, "B", created_by="admin")
        svc.create(TENANT, CampaignId("other-campaign"), "C", created_by="admin")
        pipelines = svc.list_for_campaign(TENANT, CAMPAIGN)
        assert len(pipelines) == 2
        names = {p.name for p in pipelines}
        assert names == {"A", "B"}


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle transitions
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineLifecycle:
    def _setup(self) -> tuple[PipelineService, Pipeline]:
        repo = _FakePipelineRepository()
        svc = _svc(repo)
        p = svc.create(TENANT, CAMPAIGN, "Work Queue", created_by="admin")
        return svc, p

    def test_draft_to_active(self) -> None:
        svc, p = self._setup()
        activated = svc.activate(TENANT, PipelineId(p.pipeline_id), "admin")
        assert activated.status == PipelineStatus.ACTIVE

    def test_active_to_paused(self) -> None:
        svc, p = self._setup()
        svc.activate(TENANT, PipelineId(p.pipeline_id), "admin")
        paused = svc.pause(TENANT, PipelineId(p.pipeline_id), "admin")
        assert paused.status == PipelineStatus.PAUSED

    def test_paused_to_active(self) -> None:
        svc, p = self._setup()
        svc.activate(TENANT, PipelineId(p.pipeline_id), "admin")
        svc.pause(TENANT, PipelineId(p.pipeline_id), "admin")
        resumed = svc.activate(TENANT, PipelineId(p.pipeline_id), "admin")
        assert resumed.status == PipelineStatus.ACTIVE

    def test_any_status_to_archived(self) -> None:
        for fn_name, transition_fn in [
            ("draft", lambda svc, pid: None),
            ("active", lambda svc, pid: svc.activate(TENANT, pid, "admin")),
        ]:
            repo = _FakePipelineRepository()
            svc = _svc(repo)
            p = svc.create(TENANT, CAMPAIGN, "P", created_by="admin")
            pid = PipelineId(p.pipeline_id)
            transition_fn(svc, pid)
            archived = svc.archive(TENANT, pid, "admin")
            assert archived.status == PipelineStatus.ARCHIVED, f"from {fn_name}"

    def test_draft_to_paused_invalid(self) -> None:
        svc, p = self._setup()
        with pytest.raises(InvalidPipelineTransitionError):
            svc.pause(TENANT, PipelineId(p.pipeline_id), "admin")

    def test_archived_immutable(self) -> None:
        svc, p = self._setup()
        pid = PipelineId(p.pipeline_id)
        svc.archive(TENANT, pid, "admin")
        with pytest.raises(InvalidPipelineTransitionError):
            svc.activate(TENANT, pid, "admin")

    def test_not_found_raises(self) -> None:
        svc = _svc()
        with pytest.raises(PipelineNotFoundError):
            svc.activate(TENANT, PipelineId(str(uuid.uuid4())), "admin")

    def test_rename_succeeds(self) -> None:
        svc, p = self._setup()
        renamed = svc.rename(TENANT, PipelineId(p.pipeline_id), "New Name")
        assert renamed.name == "New Name"

    def test_rename_archived_raises(self) -> None:
        svc, p = self._setup()
        pid = PipelineId(p.pipeline_id)
        svc.archive(TENANT, pid, "admin")
        with pytest.raises(InvalidPipelineTransitionError):
            svc.rename(TENANT, pid, "Should Fail")
