"""ReportGenerator -- the 9-type AI report catalog (ADR-006 Sec 6).

Duplication fix (ADR-006 Sec 6.1/13.5): the Executive Summary and
Daily/Weekly/Monthly Health / Performance reports NEVER recompute
``gross_recovery_rate``, ``MTTR``, ``MTBF``, ``cost_per_conversation``, etc.
independently -- they call the pre-existing ``BIPlatformService``/
``OpsAnalytics`` interfaces (via the narrow ports below) as their sole KPI
source, and add only two things: an LLM ``narrative`` and the
``verified_facts``/``hypotheses`` evidence split. Every such KPI call is
captured as a :class:`SourceServiceCall` snapshot (Sec 6.2/13.8) so the
report's numeric content is reproducible even where it isn't backed by an
``Insight``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol

from src.services.ops_intelligence.models import (
    ConfidenceLevel,
    Insight,
    InsightCategory,
    Report,
    ReportType,
    ScopeLevel,
    Severity,
    SourceServiceCall,
    VerifiedFact,
)
from src.services.ops_intelligence.reasoning.adapters.protocol import NarrationRequest, ReasoningAdapter
from src.services.ops_intelligence.reasoning.repository import ReportRepositoryPort


class ExecutiveSummaryLike(Protocol):
    gross_recovery_rate: float
    cost_per_conversation_minor: int
    mom_improvement: float
    slo_attainment: float
    compliance_score: float


class ExecutiveSummarySourcePort(Protocol):
    """Wraps the existing ``bi_platform.ExecutiveDashboard.get_executive_summary()`` -- never reimplemented."""

    def get_executive_summary(self, tenant_id: str, day: datetime) -> ExecutiveSummaryLike: ...


class TechnicalKPIsLike(Protocol):
    availability: float
    mttr_seconds: float
    mtbf_seconds: float
    slo_attainment: float


class BusinessKPIsLike(Protocol):
    revenue_minor: int
    collections_recovered_minor: int
    cost_per_call_minor: int


class OperatorScorecardLike(Protocol):
    technical_kpis: TechnicalKPIsLike
    business_kpis: BusinessKPIsLike


class OperatorScorecardSourcePort(Protocol):
    """Wraps the existing ``ops_analytics.OpsAnalytics.get_operator_scorecard()`` -- never reimplemented."""

    def get_operator_scorecard(self, date_range: object) -> OperatorScorecardLike: ...


class ReportGenerator:
    def __init__(
        self,
        repository: ReportRepositoryPort,
        reasoning_adapter: ReasoningAdapter,
        *,
        executive_summary_source: ExecutiveSummarySourcePort | None = None,
        operator_scorecard_source: OperatorScorecardSourcePort | None = None,
    ) -> None:
        self._repository = repository
        self._reasoning_adapter = reasoning_adapter
        self._executive_summary_source = executive_summary_source
        self._operator_scorecard_source = operator_scorecard_source

    # ------------------------------------------------------------------
    # Tenant-scoped: Executive / Engineering Summary (Sec 6.1 duplication fix)
    # ------------------------------------------------------------------

    async def generate_executive_summary(
        self, tenant_id: str, *, period_start: datetime, period_end: datetime
    ) -> Report:
        if self._executive_summary_source is None:
            raise RuntimeError("ReportGenerator requires an ExecutiveSummarySourcePort to generate Executive Summaries")

        called_at = datetime.now(UTC)
        summary = self._executive_summary_source.get_executive_summary(tenant_id, period_end)
        call = SourceServiceCall(
            service="bi_platform.ExecutiveDashboard",
            method="get_executive_summary",
            params={"tenant_id": tenant_id, "day": period_end.isoformat()},
            result_snapshot={
                "gross_recovery_rate": summary.gross_recovery_rate,
                "cost_per_conversation_minor": summary.cost_per_conversation_minor,
                "mom_improvement": summary.mom_improvement,
                "slo_attainment": summary.slo_attainment,
                "compliance_score": summary.compliance_score,
            },
            called_at=called_at,
        )

        facts = (
            VerifiedFact(
                claim=f"Gross recovery rate {summary.gross_recovery_rate:.1%}, "
                f"MoM change {summary.mom_improvement:+.1%}, SLO attainment {summary.slo_attainment:.1%}",
                source="bi_platform",
                query=f"ExecutiveDashboard.get_executive_summary(tenant_id={tenant_id})",
                value=str(summary.gross_recovery_rate),
                observed_at=called_at,
            ),
        )
        narration = await self._reasoning_adapter.narrate(
            NarrationRequest(
                category=InsightCategory.SUMMARY,
                tenant_id=tenant_id,
                verified_facts=facts,
                candidate_affected_components=("business_kpis",),
            )
        )
        severity = Severity.WARNING if summary.mom_improvement < 0 else Severity.INFO
        return self._persist(
            report_type=ReportType.EXECUTIVE_SUMMARY,
            scope=ScopeLevel.TENANT,
            tenant_id=tenant_id,
            period_start=period_start,
            period_end=period_end,
            severity=severity,
            affected_components=("collections", "billing"),
            business_impact=(
                f"Recovery rate {summary.gross_recovery_rate:.1%} ({summary.mom_improvement:+.1%} month-over-month); "
                f"cost per conversation {summary.cost_per_conversation_minor} minor units."
            ),
            recommended_actions=(narration.recommendation,) if narration.recommendation else (),
            confidence_level=narration.confidence_level,
            evidence=(),
            source_service_calls=(call,),
            narrative=narration.narrative,
        )

    async def generate_engineering_summary(
        self, *, period_start: datetime, period_end: datetime, insights: tuple[Insight, ...]
    ) -> Report:
        """Same underlying evidence as Executive Summary, technical-framed narrative (Sec 6.2)."""
        facts = tuple(f for insight in insights for f in insight.verified_facts)
        narration = await self._reasoning_adapter.narrate(
            NarrationRequest(
                category=insights[0].category if insights else _summary_category(),
                tenant_id=None,
                verified_facts=facts or (_placeholder_fact(period_end),),
                candidate_affected_components=tuple({c for i in insights for c in i.affected_components}),
            )
        )
        worst_severity = _max_severity((i.severity for i in insights), default=Severity.INFO)
        return self._persist(
            report_type=ReportType.ENGINEERING_SUMMARY,
            scope=ScopeLevel.PLATFORM,
            tenant_id=None,
            period_start=period_start,
            period_end=period_end,
            severity=worst_severity,
            affected_components=tuple({c for i in insights for c in i.affected_components}),
            business_impact=f"{len(insights)} operational insight(s) generated this period.",
            recommended_actions=tuple(i.recommendation for i in insights if i.recommendation),
            confidence_level=narration.confidence_level,
            evidence=insights,
            source_service_calls=(),
            narrative=narration.narrative,
        )

    # ------------------------------------------------------------------
    # Platform-wide health/performance reports (Sec 6.1 duplication fix)
    # ------------------------------------------------------------------

    async def generate_daily_health(self, *, period_start: datetime, period_end: datetime, date_range: object) -> Report:
        return await self._generate_scorecard_report(ReportType.DAILY_HEALTH, period_start, period_end, date_range)

    async def generate_weekly_health(self, *, period_start: datetime, period_end: datetime, date_range: object) -> Report:
        return await self._generate_scorecard_report(ReportType.WEEKLY_HEALTH, period_start, period_end, date_range)

    async def generate_monthly_health(self, *, period_start: datetime, period_end: datetime, date_range: object) -> Report:
        return await self._generate_scorecard_report(ReportType.MONTHLY_HEALTH, period_start, period_end, date_range)

    async def generate_performance_report(
        self, *, period_start: datetime, period_end: datetime, date_range: object
    ) -> Report:
        return await self._generate_scorecard_report(ReportType.PERFORMANCE_REPORT, period_start, period_end, date_range)

    async def _generate_scorecard_report(
        self, report_type: ReportType, period_start: datetime, period_end: datetime, date_range: object
    ) -> Report:
        if self._operator_scorecard_source is None:
            raise RuntimeError("ReportGenerator requires an OperatorScorecardSourcePort for scorecard-based reports")

        called_at = datetime.now(UTC)
        scorecard = self._operator_scorecard_source.get_operator_scorecard(date_range)
        technical = scorecard.technical_kpis
        business = scorecard.business_kpis
        call = SourceServiceCall(
            service="ops_analytics.OpsAnalytics",
            method="get_operator_scorecard",
            params={"date_range": str(date_range)},
            result_snapshot={
                "availability": technical.availability,
                "mttr_seconds": technical.mttr_seconds,
                "mtbf_seconds": technical.mtbf_seconds,
                "slo_attainment": technical.slo_attainment,
                "revenue_minor": business.revenue_minor,
                "collections_recovered_minor": business.collections_recovered_minor,
                "cost_per_call_minor": business.cost_per_call_minor,
            },
            called_at=called_at,
        )
        facts = (
            VerifiedFact(
                claim=f"Availability {technical.availability:.2%}, MTTR {technical.mttr_seconds:.0f}s, "
                f"SLO attainment {technical.slo_attainment:.2%}",
                source="ops_analytics",
                query="OpsAnalytics.get_operator_scorecard",
                value=str(technical.availability),
                observed_at=called_at,
            ),
        )
        narration = await self._reasoning_adapter.narrate(
            NarrationRequest(
                category=_summary_category(),
                tenant_id=None,
                verified_facts=facts,
                candidate_affected_components=("infrastructure",),
            )
        )
        severity = Severity.CRITICAL if technical.availability < 0.99 else (
            Severity.WARNING if technical.slo_attainment < 0.99 else Severity.INFO
        )
        return self._persist(
            report_type=report_type,
            scope=ScopeLevel.PLATFORM,
            tenant_id=None,
            period_start=period_start,
            period_end=period_end,
            severity=severity,
            affected_components=("infrastructure",),
            business_impact=f"Revenue {business.revenue_minor} minor units; cost per call {business.cost_per_call_minor}.",
            recommended_actions=(narration.recommendation,) if narration.recommendation else (),
            confidence_level=narration.confidence_level,
            evidence=(),
            source_service_calls=(call,),
            narrative=narration.narrative,
        )

    # ------------------------------------------------------------------
    # Incident-derived reports: no external KPI source, built purely from Insights
    # ------------------------------------------------------------------

    async def generate_incident_report(
        self, insights: tuple[Insight, ...], *, tenant_id: str | None, period_start: datetime, period_end: datetime
    ) -> Report:
        return await self._generate_from_insights(ReportType.INCIDENT_REPORT, insights, tenant_id, period_start, period_end)

    async def generate_rca(
        self, insight: Insight, *, period_start: datetime, period_end: datetime
    ) -> Report:
        return await self._generate_from_insights(ReportType.RCA, (insight,), insight.tenant_id, period_start, period_end)

    async def _generate_from_insights(
        self,
        report_type: ReportType,
        insights: tuple[Insight, ...],
        tenant_id: str | None,
        period_start: datetime,
        period_end: datetime,
    ) -> Report:
        if not insights:
            raise ValueError(f"{report_type.value} requires at least one Insight")
        facts = tuple(f for insight in insights for f in insight.verified_facts)
        narration = await self._reasoning_adapter.narrate(
            NarrationRequest(
                category=insights[0].category,
                tenant_id=tenant_id,
                verified_facts=facts,
                candidate_affected_components=tuple({c for i in insights for c in i.affected_components}),
            )
        )
        scope = ScopeLevel.TENANT if tenant_id is not None else ScopeLevel.PLATFORM
        worst_severity = _max_severity((i.severity for i in insights), default=Severity.INFO)
        return self._persist(
            report_type=report_type,
            scope=scope,
            tenant_id=tenant_id,
            period_start=period_start,
            period_end=period_end,
            severity=worst_severity,
            affected_components=tuple({c for i in insights for c in i.affected_components}),
            business_impact=f"{len(insights)} insight(s) contributed to this {report_type.value}.",
            recommended_actions=tuple(i.recommendation for i in insights if i.recommendation),
            confidence_level=narration.confidence_level,
            evidence=insights,
            source_service_calls=(),
            narrative=narration.narrative,
        )

    # ------------------------------------------------------------------
    # Capacity report -- built from CapacityPlanner output (capacity_planner.py)
    # ------------------------------------------------------------------

    async def generate_capacity_report(
        self,
        forecasts: tuple[object, ...],
        *,
        period_start: datetime,
        period_end: datetime,
    ) -> Report:
        called_at = datetime.now(UTC)
        facts = tuple(
            VerifiedFact(
                claim=f"{getattr(f, 'resource', 'resource')} headroom {getattr(f, 'headroom_pct', 0):.1f}% "
                f"over {getattr(f, 'horizon_days', 0)} days",
                source="capacity_planner",
                query=f"CapacityPlanner.forecast(resource={getattr(f, 'resource', '?')})",
                value=str(getattr(f, "headroom_pct", 0)),
                observed_at=called_at,
            )
            for f in forecasts
        ) or (_placeholder_fact(called_at),)
        narration = await self._reasoning_adapter.narrate(
            NarrationRequest(
                category=InsightCategory.PREDICTION,
                tenant_id=None,
                verified_facts=facts,
                candidate_affected_components=tuple({str(getattr(f, "resource", "")) for f in forecasts}),
            )
        )
        min_headroom = min((getattr(f, "headroom_pct", 100.0) for f in forecasts), default=100.0)
        severity = Severity.CRITICAL if min_headroom < 10 else (Severity.WARNING if min_headroom < 25 else Severity.INFO)
        return self._persist(
            report_type=ReportType.CAPACITY_REPORT,
            scope=ScopeLevel.PLATFORM,
            tenant_id=None,
            period_start=period_start,
            period_end=period_end,
            severity=severity,
            affected_components=tuple({str(getattr(f, "resource", "")) for f in forecasts}),
            business_impact=f"Minimum forecasted headroom across resources: {min_headroom:.1f}%.",
            recommended_actions=(narration.recommendation,) if narration.recommendation else (),
            confidence_level=narration.confidence_level,
            evidence=(),
            source_service_calls=(),
            narrative=narration.narrative,
        )

    # ------------------------------------------------------------------

    def _persist(
        self,
        *,
        report_type: ReportType,
        scope: ScopeLevel,
        tenant_id: str | None,
        period_start: datetime,
        period_end: datetime,
        severity: Severity,
        affected_components: tuple[str, ...],
        business_impact: str,
        recommended_actions: tuple[str, ...],
        confidence_level: ConfidenceLevel,
        evidence: tuple[Insight, ...],
        source_service_calls: tuple[SourceServiceCall, ...],
        narrative: str,
    ) -> Report:
        report = Report(
            report_id=str(uuid.uuid4()),
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
            scope_level=scope,
            tenant_id=tenant_id,
            severity=severity,
            affected_components=affected_components,
            business_impact=business_impact,
            recommended_actions=recommended_actions,
            confidence_level=confidence_level,
            evidence=evidence,
            source_service_calls=source_service_calls,
            narrative=narrative,
            generated_at=datetime.now(UTC),
        )
        return self._repository.create(report)


def _summary_category() -> InsightCategory:
    return InsightCategory.SUMMARY


def _placeholder_fact(at: datetime) -> VerifiedFact:
    return VerifiedFact(claim="No specific evidence available for this period", source="event_bus", query="n/a", value="0", observed_at=at)


def _max_severity(severities: Iterable[Severity], *, default: Severity) -> Severity:
    order = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}
    values = list(severities)
    if not values:
        return default
    return max(values, key=lambda s: order[s])


__all__ = ["ExecutiveSummarySourcePort", "OperatorScorecardSourcePort", "ReportGenerator"]
