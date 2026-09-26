"""Unit tests for ReportGenerator (ADR-006 Sec 6/13.5/13.8).

Central assertion across these tests: Executive Summary / Health / Performance
reports call the EXISTING BIPlatformService/OpsAnalytics KPI sources and never
recompute the numbers themselves (Sec 6.1 duplication fix) -- verified by
asserting the fake KPI source was actually called and its snapshot lands in
``source_service_calls`` (Sec 13.8 reproducibility).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from src.services.ops_intelligence.models import (
    ConfidenceLevel,
    Hypothesis,
    Insight,
    InsightCategory,
    Report,
    ReportType,
    ScopeLevel,
    Severity,
    VerifiedFact,
)
from src.services.ops_intelligence.reasoning.adapters.protocol import NarrationRequest, NarrationResult
from src.services.ops_intelligence.reasoning.report_generator import ReportGenerator

PERIOD_START = datetime(2026, 7, 18, tzinfo=UTC)
PERIOD_END = datetime(2026, 7, 25, tzinfo=UTC)


class _FakeReportRepository:
    def __init__(self) -> None:
        self.rows: dict[str, Report] = {}

    def create(self, report: Report) -> Report:
        self.rows[report.report_id] = report
        return report

    def get(self, report_id: str) -> Report | None:
        return self.rows.get(report_id)

    def list(self, *, tenant_id=None, report_type=None, limit: int = 50) -> tuple[Report, ...]:
        return tuple(self.rows.values())[:limit]


class _FakeReasoningAdapter:
    def __init__(self, recommendation: str | None = "investigate") -> None:
        self.calls: list[NarrationRequest] = []
        self._recommendation = recommendation

    async def narrate(self, request: NarrationRequest) -> NarrationResult:
        self.calls.append(request)
        return NarrationResult(
            hypotheses=(),
            recommendation=self._recommendation,
            confidence_level=ConfidenceLevel.MEDIUM,
            narrative="synthesized narrative",
            model="claude-sonnet-5",
            prompt_version="v1",
        )


@dataclass(frozen=True)
class _FakeExecutiveSummary:
    gross_recovery_rate: float
    cost_per_conversation_minor: int
    mom_improvement: float
    slo_attainment: float
    compliance_score: float


class _FakeExecutiveSummarySource:
    def __init__(self, summary: _FakeExecutiveSummary) -> None:
        self._summary = summary
        self.calls: list[tuple[str, datetime]] = []

    def get_executive_summary(self, tenant_id: str, day: datetime) -> _FakeExecutiveSummary:
        self.calls.append((tenant_id, day))
        return self._summary


@dataclass(frozen=True)
class _FakeTechnicalKPIs:
    availability: float
    mttr_seconds: float
    mtbf_seconds: float
    slo_attainment: float


@dataclass(frozen=True)
class _FakeBusinessKPIs:
    revenue_minor: int
    collections_recovered_minor: int
    cost_per_call_minor: int


@dataclass(frozen=True)
class _FakeScorecard:
    technical_kpis: _FakeTechnicalKPIs
    business_kpis: _FakeBusinessKPIs


class _FakeOperatorScorecardSource:
    def __init__(self, scorecard: _FakeScorecard) -> None:
        self._scorecard = scorecard
        self.calls: list[object] = []

    def get_operator_scorecard(self, date_range: object) -> _FakeScorecard:
        self.calls.append(date_range)
        return self._scorecard


def _insight(claim: str = "regression detected", severity: Severity = Severity.WARNING) -> Insight:
    return Insight(
        insight_id="ins-1",
        tenant_id=None,
        category=InsightCategory.REGRESSION,
        severity=severity,
        verified_facts=(VerifiedFact(claim=claim, source="prometheus", query="q", value="1", observed_at=PERIOD_END),),
        hypotheses=(Hypothesis(claim="thermal throttling", reasoning="known pattern", confidence=ConfidenceLevel.MEDIUM),),
        affected_components=("tts",),
        confidence_level=ConfidenceLevel.MEDIUM,
        model="claude-sonnet-5",
        prompt_version="v1",
        generated_at=PERIOD_END,
        recommendation="check GPU temp",
    )


class TestExecutiveSummaryDuplicationFix:
    @pytest.mark.asyncio
    async def test_calls_existing_bi_platform_source_not_reimplemented(self) -> None:
        summary = _FakeExecutiveSummary(0.72, 150, 0.03, 0.99, 0.95)
        source = _FakeExecutiveSummarySource(summary)
        generator = ReportGenerator(_FakeReportRepository(), _FakeReasoningAdapter(), executive_summary_source=source)

        report = await generator.generate_executive_summary("tenant-1", period_start=PERIOD_START, period_end=PERIOD_END)

        assert len(source.calls) == 1  # the ONLY source of these numbers
        assert report.report_type == ReportType.EXECUTIVE_SUMMARY
        assert report.scope_level == ScopeLevel.TENANT
        assert report.tenant_id == "tenant-1"
        assert len(report.source_service_calls) == 1
        assert report.source_service_calls[0].service == "bi_platform.ExecutiveDashboard"
        assert report.source_service_calls[0].result_snapshot["gross_recovery_rate"] == 0.72

    @pytest.mark.asyncio
    async def test_negative_momentum_is_flagged_warning(self) -> None:
        summary = _FakeExecutiveSummary(0.5, 200, -0.10, 0.95, 0.90)
        source = _FakeExecutiveSummarySource(summary)
        generator = ReportGenerator(_FakeReportRepository(), _FakeReasoningAdapter(), executive_summary_source=source)
        report = await generator.generate_executive_summary("tenant-1", period_start=PERIOD_START, period_end=PERIOD_END)
        assert report.severity == Severity.WARNING

    @pytest.mark.asyncio
    async def test_missing_source_raises_clear_error(self) -> None:
        generator = ReportGenerator(_FakeReportRepository(), _FakeReasoningAdapter())
        with pytest.raises(RuntimeError, match="ExecutiveSummarySourcePort"):
            await generator.generate_executive_summary("tenant-1", period_start=PERIOD_START, period_end=PERIOD_END)


class TestScorecardBasedReportsDuplicationFix:
    @pytest.mark.asyncio
    async def test_daily_health_calls_existing_ops_analytics_source(self) -> None:
        scorecard = _FakeScorecard(_FakeTechnicalKPIs(0.9995, 300, 86400, 0.99), _FakeBusinessKPIs(500_000, 1_200_000, 50))
        source = _FakeOperatorScorecardSource(scorecard)
        generator = ReportGenerator(_FakeReportRepository(), _FakeReasoningAdapter(), operator_scorecard_source=source)

        report = await generator.generate_daily_health(period_start=PERIOD_START, period_end=PERIOD_END, date_range="2026-07-25")

        assert len(source.calls) == 1
        assert report.report_type == ReportType.DAILY_HEALTH
        assert report.scope_level == ScopeLevel.PLATFORM
        assert report.source_service_calls[0].service == "ops_analytics.OpsAnalytics"
        assert report.source_service_calls[0].result_snapshot["availability"] == 0.9995

    @pytest.mark.asyncio
    async def test_low_availability_is_critical(self) -> None:
        scorecard = _FakeScorecard(_FakeTechnicalKPIs(0.95, 3000, 3600, 0.90), _FakeBusinessKPIs(0, 0, 0))
        source = _FakeOperatorScorecardSource(scorecard)
        generator = ReportGenerator(_FakeReportRepository(), _FakeReasoningAdapter(), operator_scorecard_source=source)
        report = await generator.generate_weekly_health(period_start=PERIOD_START, period_end=PERIOD_END, date_range="week")
        assert report.severity == Severity.CRITICAL

    @pytest.mark.asyncio
    async def test_performance_report_uses_same_scorecard_source(self) -> None:
        scorecard = _FakeScorecard(_FakeTechnicalKPIs(0.999, 100, 10000, 0.999), _FakeBusinessKPIs(1, 1, 1))
        source = _FakeOperatorScorecardSource(scorecard)
        generator = ReportGenerator(_FakeReportRepository(), _FakeReasoningAdapter(), operator_scorecard_source=source)
        report = await generator.generate_performance_report(period_start=PERIOD_START, period_end=PERIOD_END, date_range="range")
        assert report.report_type == ReportType.PERFORMANCE_REPORT
        assert len(source.calls) == 1


class TestInsightDerivedReports:
    @pytest.mark.asyncio
    async def test_rca_from_single_insight(self) -> None:
        generator = ReportGenerator(_FakeReportRepository(), _FakeReasoningAdapter())
        report = await generator.generate_rca(_insight(), period_start=PERIOD_START, period_end=PERIOD_END)
        assert report.report_type == ReportType.RCA
        assert len(report.evidence) == 1
        assert report.evidence[0].verified_facts[0].claim == "regression detected"

    @pytest.mark.asyncio
    async def test_incident_report_aggregates_multiple_insights(self) -> None:
        generator = ReportGenerator(_FakeReportRepository(), _FakeReasoningAdapter())
        insights = (_insight("issue A", Severity.WARNING), _insight("issue B", Severity.CRITICAL))
        report = await generator.generate_incident_report(insights, tenant_id="tenant-2", period_start=PERIOD_START, period_end=PERIOD_END)
        assert report.severity == Severity.CRITICAL  # worst-of
        assert report.scope_level == ScopeLevel.TENANT
        assert len(report.evidence) == 2

    @pytest.mark.asyncio
    async def test_incident_report_requires_at_least_one_insight(self) -> None:
        generator = ReportGenerator(_FakeReportRepository(), _FakeReasoningAdapter())
        with pytest.raises(ValueError, match="requires at least one Insight"):
            await generator.generate_incident_report((), tenant_id=None, period_start=PERIOD_START, period_end=PERIOD_END)


class TestCapacityReport:
    @dataclass(frozen=True)
    class _FakeForecast:
        resource: str
        horizon_days: int
        headroom_pct: float

    @pytest.mark.asyncio
    async def test_low_headroom_is_critical(self) -> None:
        generator = ReportGenerator(_FakeReportRepository(), _FakeReasoningAdapter())
        forecasts = (self._FakeForecast("gpu", 90, 5.0), self._FakeForecast("cpu", 90, 60.0))
        report = await generator.generate_capacity_report(forecasts, period_start=PERIOD_START, period_end=PERIOD_END)
        assert report.report_type == ReportType.CAPACITY_REPORT
        assert report.severity == Severity.CRITICAL

    @pytest.mark.asyncio
    async def test_ample_headroom_is_info(self) -> None:
        generator = ReportGenerator(_FakeReportRepository(), _FakeReasoningAdapter())
        forecasts = (self._FakeForecast("redis", 30, 80.0),)
        report = await generator.generate_capacity_report(forecasts, period_start=PERIOD_START, period_end=PERIOD_END)
        assert report.severity == Severity.INFO


class TestSchemaReconstructibility:
    """ADR-006 Sec 13.8 -- reports must be reproducible from stored evidence + source_service_calls."""

    @pytest.mark.asyncio
    async def test_report_is_persisted_and_retrievable(self) -> None:
        repo = _FakeReportRepository()
        summary = _FakeExecutiveSummary(0.72, 150, 0.03, 0.99, 0.95)
        source = _FakeExecutiveSummarySource(summary)
        generator = ReportGenerator(repo, _FakeReasoningAdapter(), executive_summary_source=source)
        report = await generator.generate_executive_summary("tenant-1", period_start=PERIOD_START, period_end=PERIOD_END)

        stored = repo.get(report.report_id)
        assert stored is not None
        assert stored.narrative == "synthesized narrative"
        assert stored.confidence_level == ConfidenceLevel.MEDIUM
