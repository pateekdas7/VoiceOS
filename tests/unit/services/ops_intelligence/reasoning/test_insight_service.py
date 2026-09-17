"""Unit tests for InsightService (ADR-006 Sec 3.2.2/3.5)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.services.ops_intelligence.models import (
    ConfidenceLevel,
    Hypothesis,
    Insight,
    InsightCategory,
    PatternSignature,
    Severity,
    VerifiedFact,
)
from src.services.ops_intelligence.reasoning.adapters.protocol import NarrationRequest, NarrationResult
from src.services.ops_intelligence.reasoning.evidence_bundler import EvidenceBundle
from src.services.ops_intelligence.reasoning.insight_service import InsightService

NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)


class _FakeInsightRepository:
    def __init__(self) -> None:
        self.rows: dict[str, Insight] = {}

    def create(self, insight: Insight) -> Insight:
        self.rows[insight.insight_id] = insight
        return insight

    def get(self, insight_id: str) -> Insight | None:
        return self.rows.get(insight_id)

    def list(self, *, tenant_id=None, category=None, limit: int = 50) -> tuple[Insight, ...]:
        return tuple(self.rows.values())[:limit]


class _FakePatternRepository:
    def __init__(self) -> None:
        self.signatures: dict[str, PatternSignature] = {}

    def find(self, fingerprint_hash: str, *, tenant_id: str | None) -> PatternSignature | None:
        return self.signatures.get(fingerprint_hash)

    def upsert_occurrence(self, signature: PatternSignature) -> PatternSignature:
        self.signatures[signature.fingerprint_hash] = signature
        return signature


class _FakeReasoningAdapter:
    def __init__(self, result: NarrationResult) -> None:
        self._result = result
        self.calls: list[NarrationRequest] = []

    async def narrate(self, request: NarrationRequest) -> NarrationResult:
        self.calls.append(request)
        return self._result


def _evidence(category: InsightCategory = InsightCategory.REGRESSION) -> EvidenceBundle:
    return EvidenceBundle(
        category=category,
        severity=Severity.WARNING,
        verified_facts=(
            VerifiedFact(claim="latency up", source="prometheus", query="q1", value="900", observed_at=NOW),
        ),
        affected_components=("tts",),
        tenant_id=None,
    )


def _narration(confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM, claim: str = "thermal throttling") -> NarrationResult:
    return NarrationResult(
        hypotheses=(Hypothesis(claim=claim, reasoning="matches known pattern", confidence=confidence),),
        recommendation="check GPU temp",
        confidence_level=confidence,
        narrative="latency regressed",
        model="claude-sonnet-5",
        prompt_version="v1",
    )


class TestGenerate:
    @pytest.mark.asyncio
    async def test_generates_and_persists_insight(self) -> None:
        repo = _FakeInsightRepository()
        service = InsightService(repo, _FakeReasoningAdapter(_narration()))
        insight = await service.generate(_evidence())

        assert insight is not None
        assert insight.insight_id in repo.rows
        assert insight.severity == Severity.WARNING
        assert len(insight.verified_facts) == 1
        assert insight.hypotheses[0].claim == "thermal throttling"

    @pytest.mark.asyncio
    async def test_no_verified_facts_returns_none_without_persisting(self) -> None:
        repo = _FakeInsightRepository()
        service = InsightService(repo, _FakeReasoningAdapter(_narration()))
        empty_evidence = EvidenceBundle(
            category=InsightCategory.REGRESSION, severity=Severity.WARNING, verified_facts=(), affected_components=(), tenant_id=None
        )
        result = await service.generate(empty_evidence)
        assert result is None
        assert repo.rows == {}

    @pytest.mark.asyncio
    async def test_reasoning_adapter_receives_the_evidence(self) -> None:
        adapter = _FakeReasoningAdapter(_narration())
        service = InsightService(_FakeInsightRepository(), adapter)
        await service.generate(_evidence())
        assert len(adapter.calls) == 1
        assert adapter.calls[0].verified_facts[0].claim == "latency up"


class _FakeUsagePublisher:
    def __init__(self) -> None:
        self.published: list[object] = []

    def publish(self, event: object) -> None:
        self.published.append(event)


class _FakeCostObservability:
    def __init__(self) -> None:
        self.recorded_tokens: list[int] = []

    def record_platform_wide_tokens(self, token_count: int) -> None:
        self.recorded_tokens.append(token_count)


def _evidence_for_tenant(tenant_id: str | None) -> EvidenceBundle:
    return EvidenceBundle(
        category=InsightCategory.REGRESSION,
        severity=Severity.WARNING,
        verified_facts=(VerifiedFact(claim="latency up", source="prometheus", query="q1", value="900", observed_at=NOW),),
        affected_components=("tts",),
        tenant_id=tenant_id,
    )


def _narration_with_tokens(total_tokens: int) -> NarrationResult:
    return NarrationResult(
        hypotheses=(),
        recommendation=None,
        confidence_level=ConfidenceLevel.MEDIUM,
        narrative="x",
        model="claude-sonnet-5",
        prompt_version="v1",
        total_tokens=total_tokens,
    )


class TestCostMeteringWiring:
    """ADR-006 Sec 13.10 -- tenant-scoped runs publish a usage event; platform-wide runs meter via Prometheus."""

    @pytest.mark.asyncio
    async def test_tenant_scoped_run_publishes_usage_event(self) -> None:
        publisher = _FakeUsagePublisher()
        adapter = _FakeReasoningAdapter(_narration_with_tokens(1500))
        service = InsightService(_FakeInsightRepository(), adapter, usage_publisher=publisher)

        insight = await service.generate(_evidence_for_tenant("tenant-1"))

        assert len(publisher.published) == 1
        assert publisher.published[0].tenant_id == "tenant-1"
        assert publisher.published[0].token_count == 1500
        assert publisher.published[0].insight_id == insight.insight_id

    @pytest.mark.asyncio
    async def test_platform_wide_run_never_publishes_a_tenant_usage_event(self) -> None:
        publisher = _FakeUsagePublisher()
        cost_obs = _FakeCostObservability()
        adapter = _FakeReasoningAdapter(_narration_with_tokens(2000))
        service = InsightService(
            _FakeInsightRepository(), adapter, usage_publisher=publisher, cost_observability=cost_obs
        )

        await service.generate(_evidence_for_tenant(None))

        assert publisher.published == []
        assert cost_obs.recorded_tokens == [2000]

    @pytest.mark.asyncio
    async def test_zero_tokens_records_nothing(self) -> None:
        publisher = _FakeUsagePublisher()
        cost_obs = _FakeCostObservability()
        adapter = _FakeReasoningAdapter(_narration_with_tokens(0))
        service = InsightService(
            _FakeInsightRepository(), adapter, usage_publisher=publisher, cost_observability=cost_obs
        )

        await service.generate(_evidence_for_tenant("tenant-1"))
        await service.generate(_evidence_for_tenant(None))

        assert publisher.published == []
        assert cost_obs.recorded_tokens == []

    @pytest.mark.asyncio
    async def test_no_ports_wired_is_a_no_op(self) -> None:
        adapter = _FakeReasoningAdapter(_narration_with_tokens(5000))
        service = InsightService(_FakeInsightRepository(), adapter)
        insight = await service.generate(_evidence_for_tenant("tenant-1"))
        assert insight is not None  # never raises even with no cost ports wired


class TestRecurringPatternDetection:
    @pytest.mark.asyncio
    async def test_first_occurrence_creates_a_new_signature(self) -> None:
        patterns = _FakePatternRepository()
        service = InsightService(_FakeInsightRepository(), _FakeReasoningAdapter(_narration()), patterns)
        await service.generate(_evidence())

        assert len(patterns.signatures) == 1
        sig = next(iter(patterns.signatures.values()))
        assert sig.occurrence_count == 1

    @pytest.mark.asyncio
    async def test_repeated_pattern_increments_occurrence_count(self) -> None:
        patterns = _FakePatternRepository()
        adapter = _FakeReasoningAdapter(_narration(claim="same root cause"))
        service = InsightService(_FakeInsightRepository(), adapter, patterns)

        await service.generate(_evidence())
        await service.generate(_evidence())
        await service.generate(_evidence())

        assert len(patterns.signatures) == 1
        sig = next(iter(patterns.signatures.values()))
        assert sig.occurrence_count == 3

    @pytest.mark.asyncio
    async def test_different_root_cause_creates_a_different_signature(self) -> None:
        patterns = _FakePatternRepository()
        service_a = InsightService(_FakeInsightRepository(), _FakeReasoningAdapter(_narration(claim="cause A")), patterns)
        service_b = InsightService(_FakeInsightRepository(), _FakeReasoningAdapter(_narration(claim="cause B")), patterns)

        await service_a.generate(_evidence())
        await service_b.generate(_evidence())

        assert len(patterns.signatures) == 2

    @pytest.mark.asyncio
    async def test_no_pattern_repository_is_a_no_op(self) -> None:
        service = InsightService(_FakeInsightRepository(), _FakeReasoningAdapter(_narration()), pattern_repository=None)
        insight = await service.generate(_evidence())
        assert insight is not None  # doesn't raise even with no pattern repository wired
