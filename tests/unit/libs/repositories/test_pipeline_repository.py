"""Unit tests for PipelineRepository using a real in-memory cursor double.

Architecture: V5 Ch6 (Campaign Engine — Lead Distribution, ADR-005 §14).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import pytest

from src.libs.contracts.models.pipeline import Pipeline, PipelineStatus
from src.libs.contracts.primitives import CampaignId, PipelineId, TenantId
from src.libs.repositories.pipeline import PipelineRepository

TENANT = TenantId("t-001")
CAMPAIGN = CampaignId("c-001")
NOW = datetime.now(UTC)


# ─────────────────────────────────────────────────────────────────────────────
# Minimal in-memory connection double (mirrors base.py test approach)
# ─────────────────────────────────────────────────────────────────────────────

class _InMemoryCursor:
    def __init__(self, store: dict[str, list[Any]]) -> None:
        self._store = store
        self._results: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        sql_upper = sql.upper().strip()
        if sql_upper.startswith("INSERT INTO PIPELINES"):
            self._store["pipelines"].append(params)
        elif sql_upper.startswith("SELECT") and "PIPELINES" in sql_upper:
            has_limit = "LIMIT %S" in sql_upper
            # params order: [tenant_id, ...filters..., limit?]
            # We parse out filters by looking at the WHERE clause conditions.
            tenant_id_filter = params[0] if params else None
            pipeline_id_filter: Any = None
            campaign_id_filter: Any = None
            status_filter: str | None = None

            extra_params = list(params[1: -1 if has_limit else None])
            if "PIPELINE_ID = %S" in sql_upper:
                pipeline_id_filter = extra_params[0] if extra_params else None
            if "CAMPAIGN_ID = %S" in sql_upper and "STATUS = 'ACTIVE'" not in sql_upper:
                campaign_id_filter = extra_params[0] if extra_params else None
            if "STATUS = 'ACTIVE'" in sql_upper:
                campaign_id_filter = extra_params[0] if extra_params else None
                status_filter = "ACTIVE"

            filtered = []
            for r in self._store["pipelines"]:
                if tenant_id_filter and str(r[1]) != str(tenant_id_filter):
                    continue
                if pipeline_id_filter and str(r[0]) != str(pipeline_id_filter):
                    continue
                if campaign_id_filter and str(r[2]) != str(campaign_id_filter):
                    continue
                if status_filter and r[4] != status_filter:
                    continue
                filtered.append(r)
            self._results = filtered
        elif sql_upper.startswith("UPDATE PIPELINES"):
            pass  # pipeline service tests cover this via fake repo

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._results

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._results[0] if self._results else None

    @property
    def rowcount(self) -> int:
        return len(self._results)


class _InMemoryConn:
    def __init__(self) -> None:
        self._store: dict[str, list[Any]] = defaultdict(list)
        self._committed = False

    def cursor(self) -> _InMemoryCursor:
        return _InMemoryCursor(self._store)

    def commit(self) -> None:
        self._committed = True

    def rollback(self) -> None:
        pass


def _repo() -> tuple[PipelineRepository, _InMemoryConn]:
    conn = _InMemoryConn()
    return PipelineRepository(conn), conn


def _make_pipeline(
    pipeline_id: str | None = None,
    status: PipelineStatus = PipelineStatus.DRAFT,
    campaign_id: CampaignId = CAMPAIGN,
) -> Pipeline:
    return Pipeline(
        pipeline_id=PipelineId(pipeline_id or str(uuid.uuid4())),
        tenant_id=TENANT,
        campaign_id=campaign_id,
        name="Test Pipeline",
        status=status,
        created_at=NOW,
        updated_at=NOW,
        created_by="admin",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineRepositoryCreate:
    def test_create_commits(self) -> None:
        repo, conn = _repo()
        p = _make_pipeline()
        repo.create(p)
        assert conn._committed

    def test_create_stores_pipeline(self) -> None:
        repo, conn = _repo()
        p = _make_pipeline()
        repo.create(p)
        assert len(conn._store["pipelines"]) == 1

    def test_get_returns_created_pipeline(self) -> None:
        repo, conn = _repo()
        p = _make_pipeline()
        repo.create(p)
        fetched = repo.get(TENANT, p.pipeline_id)
        assert fetched is not None
        assert fetched.pipeline_id == p.pipeline_id
        assert fetched.name == "Test Pipeline"

    def test_get_missing_returns_none(self) -> None:
        repo, _ = _repo()
        result = repo.get(TENANT, PipelineId(str(uuid.uuid4())))
        assert result is None

    def test_find_by_campaign(self) -> None:
        repo, _ = _repo()
        p1 = _make_pipeline(campaign_id=CAMPAIGN)
        p2 = _make_pipeline(campaign_id=CAMPAIGN)
        p3 = _make_pipeline(campaign_id=CampaignId("other"))
        repo.create(p1)
        repo.create(p2)
        repo.create(p3)
        results = repo.find_by_campaign(TENANT, CAMPAIGN)
        assert len(results) == 2
        ids = {r.pipeline_id for r in results}
        assert p1.pipeline_id in ids
        assert p2.pipeline_id in ids
        assert p3.pipeline_id not in ids
