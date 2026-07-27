"""Unit tests for OpsIntelligenceService (ADR-006 Sec 3.0/9/13.12 -- the kill-switch facade)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.services.ops_intelligence.models import Severity
from src.services.ops_intelligence.reasoning.evidence_bundler import EvidenceBundler, MetricCheckSpec, MetricSample
from src.services.ops_intelligence.reasoning.insight_service import InsightService
from src.services.ops_intelligence.service import PLATFORM_FLAG_TENANT_ID, REASONING_FLAG_NAME, OpsIntelligenceService

from .reasoning.test_insight_service import _FakeInsightRepository, _FakeReasoningAdapter, _narration

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


class _FakeFeatureFlags:
    def __init__(self, *, enabled: bool) -> None:
        self._enabled = enabled
        self.calls: list[tuple[str, str]] = []

    def is_enabled(self, flag_name: str, tenant_id: str) -> bool:
        self.calls.append((flag_name, tenant_id))
        return self._enabled


class _FakeMetrics:
    def __init__(self, values: dict[str, MetricSample]) -> None:
        self._values = values

    def instant(self, query: str) -> MetricSample | None:
        return self._values.get(query)


_SPEC = MetricCheckSpec(
    name="tts p95", query="cur", baseline_query="base", comparison="higher_is_worse", affected_component="tts"
)


def _build(*, flag_enabled: bool) -> tuple[OpsIntelligenceService, _FakeFeatureFlags, _FakeInsightRepository]:
    flags = _FakeFeatureFlags(enabled=flag_enabled)
    metrics = _FakeMetrics({"cur": MetricSample(1000.0, NOW), "base": MetricSample(600.0, NOW)})
    bundler = EvidenceBundler(metrics, now_fn=lambda: NOW)
    repo = _FakeInsightRepository()
    insight_service = InsightService(repo, _FakeReasoningAdapter(_narration()))
    service = OpsIntelligenceService(
        flags,
        bundler,
        insight_service,
        report_generator=None,  # not exercised in these tests
        capacity_planner=None,  # not exercised in these tests
        metric_check_specs=(_SPEC,),
    )
    return service, flags, repo


class TestKillSwitch:
    @pytest.mark.asyncio
    async def test_flag_off_produces_no_insights_and_touches_no_reasoning_code(self) -> None:
        service, flags, repo = _build(flag_enabled=False)
        result = await service.run_scheduled_analysis()

        assert result == ()
        assert repo.rows == {}  # nothing was ever persisted -- reasoning/ never ran
        assert flags.calls == [(REASONING_FLAG_NAME, PLATFORM_FLAG_TENANT_ID)]

    @pytest.mark.asyncio
    async def test_flag_on_generates_insights(self) -> None:
        service, _, repo = _build(flag_enabled=True)
        result = await service.run_scheduled_analysis()

        assert len(result) == 1
        assert len(repo.rows) == 1
        assert result[0].severity == Severity.CRITICAL

    def test_is_reasoning_enabled_checks_platform_tenant_when_none_given(self) -> None:
        service, flags, _ = _build(flag_enabled=True)
        assert service.is_reasoning_enabled(None) is True
        assert flags.calls == [(REASONING_FLAG_NAME, PLATFORM_FLAG_TENANT_ID)]

    def test_is_reasoning_enabled_checks_specific_tenant_when_given(self) -> None:
        service, flags, _ = _build(flag_enabled=True)
        service.is_reasoning_enabled("tenant-42")
        assert flags.calls == [(REASONING_FLAG_NAME, "tenant-42")]

    @pytest.mark.asyncio
    async def test_tenant_scoped_run_checks_the_tenant_flag_not_platform(self) -> None:
        service, flags, _ = _build(flag_enabled=False)
        await service.run_scheduled_analysis(tenant_id="tenant-7")
        assert flags.calls == [(REASONING_FLAG_NAME, "tenant-7")]
