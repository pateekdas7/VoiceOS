"""Unit tests for EvidenceBundler (ADR-006 Sec 3.2.4/13.11)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.services.ops_intelligence.models import InsightCategory, Severity
from src.services.ops_intelligence.reasoning.evidence_bundler import (
    DEFAULT_STALENESS_THRESHOLD_SECONDS,
    EvidenceBundler,
    MetricCheckSpec,
    MetricSample,
)

NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)


class _FakeMetrics:
    def __init__(self, values: dict[str, MetricSample | None]) -> None:
        self._values = values

    def instant(self, query: str) -> MetricSample | None:
        return self._values.get(query)


class _FakeLogs:
    def __init__(self, lines: tuple[str, ...] = ()) -> None:
        self._lines = lines

    def query(self, logql: str, *, limit: int = 20) -> tuple[str, ...]:
        return self._lines[:limit]


class _FakeTraces:
    def __init__(self, traces: tuple[str, ...] = ()) -> None:
        self._traces = traces

    def query(self, trace_query: str, *, limit: int = 5) -> tuple[str, ...]:
        return self._traces[:limit]


_SPEC = MetricCheckSpec(
    name="TTS first-clause latency p95",
    query="tts_p95_current",
    baseline_query="tts_p95_baseline",
    comparison="higher_is_worse",
    affected_component="tts",
)


class TestRegressionDetection:
    def test_no_bundle_below_warning_threshold(self) -> None:
        metrics = _FakeMetrics(
            {
                "tts_p95_current": MetricSample(620.0, NOW),
                "tts_p95_baseline": MetricSample(600.0, NOW),  # +3.3%, below 20% warning
            }
        )
        bundler = EvidenceBundler(metrics, now_fn=lambda: NOW)
        assert bundler.check_regression(_SPEC) is None

    def test_warning_severity_between_thresholds(self) -> None:
        metrics = _FakeMetrics(
            {
                "tts_p95_current": MetricSample(750.0, NOW),  # +25% vs 600 baseline
                "tts_p95_baseline": MetricSample(600.0, NOW),
            }
        )
        bundler = EvidenceBundler(metrics, now_fn=lambda: NOW)
        bundle = bundler.check_regression(_SPEC)
        assert bundle is not None
        assert bundle.severity == Severity.WARNING
        assert bundle.category == InsightCategory.REGRESSION

    def test_critical_severity_above_critical_threshold(self) -> None:
        metrics = _FakeMetrics(
            {
                "tts_p95_current": MetricSample(1000.0, NOW),  # +66.7% vs 600 baseline
                "tts_p95_baseline": MetricSample(600.0, NOW),
            }
        )
        bundler = EvidenceBundler(metrics, now_fn=lambda: NOW)
        bundle = bundler.check_regression(_SPEC)
        assert bundle is not None
        assert bundle.severity == Severity.CRITICAL

    def test_lower_is_worse_comparison_flips_sign(self) -> None:
        spec = MetricCheckSpec(
            name="availability",
            query="avail_current",
            baseline_query="avail_baseline",
            comparison="lower_is_worse",
            affected_component="api",
        )
        metrics = _FakeMetrics(
            {
                "avail_current": MetricSample(50.0, NOW),  # dropped from 100 -> 50, a "worse" direction
                "avail_baseline": MetricSample(100.0, NOW),
            }
        )
        bundler = EvidenceBundler(metrics, now_fn=lambda: NOW)
        bundle = bundler.check_regression(spec)
        assert bundle is not None
        assert bundle.severity == Severity.CRITICAL  # -50% raw = +50% "badness" signed


class TestPartialTelemetry:
    """ADR-006 Sec 13.11 -- a missing source never fabricates evidence."""

    def test_missing_current_metric_returns_none(self) -> None:
        metrics = _FakeMetrics({"tts_p95_baseline": MetricSample(600.0, NOW)})
        bundler = EvidenceBundler(metrics, now_fn=lambda: NOW)
        assert bundler.check_regression(_SPEC) is None

    def test_missing_baseline_returns_none(self) -> None:
        metrics = _FakeMetrics({"tts_p95_current": MetricSample(1000.0, NOW)})
        bundler = EvidenceBundler(metrics, now_fn=lambda: NOW)
        assert bundler.check_regression(_SPEC) is None

    def test_zero_baseline_does_not_divide_by_zero(self) -> None:
        metrics = _FakeMetrics(
            {"tts_p95_current": MetricSample(100.0, NOW), "tts_p95_baseline": MetricSample(0.0, NOW)}
        )
        bundler = EvidenceBundler(metrics, now_fn=lambda: NOW)
        assert bundler.check_regression(_SPEC) is None


class TestStaleData:
    """ADR-006 Sec 13.11 -- stale samples are excluded from verified_facts, never presented as current."""

    def test_stale_current_sample_excluded_and_no_bundle_if_all_stale(self) -> None:
        stale_time = NOW - timedelta(seconds=DEFAULT_STALENESS_THRESHOLD_SECONDS + 1)
        metrics = _FakeMetrics(
            {
                "tts_p95_current": MetricSample(1000.0, stale_time),
                "tts_p95_baseline": MetricSample(600.0, stale_time),
            }
        )
        bundler = EvidenceBundler(metrics, now_fn=lambda: NOW)
        assert bundler.check_regression(_SPEC) is None

    def test_fresh_current_with_stale_baseline_still_yields_one_fact(self) -> None:
        stale_time = NOW - timedelta(seconds=DEFAULT_STALENESS_THRESHOLD_SECONDS + 1)
        metrics = _FakeMetrics(
            {
                "tts_p95_current": MetricSample(1000.0, NOW),
                "tts_p95_baseline": MetricSample(600.0, stale_time),
            }
        )
        bundler = EvidenceBundler(metrics, now_fn=lambda: NOW)
        bundle = bundler.check_regression(_SPEC)
        assert bundle is not None
        assert len(bundle.verified_facts) == 1
        assert bundle.verified_facts[0].source == "prometheus"


class TestCorrelation:
    def test_log_correlation_added_when_logs_available(self) -> None:
        metrics = _FakeMetrics(
            {"tts_p95_current": MetricSample(1000.0, NOW), "tts_p95_baseline": MetricSample(600.0, NOW)}
        )
        bundler = EvidenceBundler(metrics, logs=_FakeLogs(("error 1", "error 2")), now_fn=lambda: NOW)
        bundle = bundler.check_regression(_SPEC)
        assert bundle is not None
        log_facts = [f for f in bundle.verified_facts if f.source == "loki"]
        assert len(log_facts) == 1
        assert log_facts[0].value == "2"

    def test_no_log_correlation_when_logs_unavailable(self) -> None:
        metrics = _FakeMetrics(
            {"tts_p95_current": MetricSample(1000.0, NOW), "tts_p95_baseline": MetricSample(600.0, NOW)}
        )
        bundler = EvidenceBundler(metrics, logs=None, now_fn=lambda: NOW)
        bundle = bundler.check_regression(_SPEC)
        assert bundle is not None
        assert not any(f.source == "loki" for f in bundle.verified_facts)

    def test_trace_correlation_added_when_traces_available(self) -> None:
        metrics = _FakeMetrics(
            {"tts_p95_current": MetricSample(1000.0, NOW), "tts_p95_baseline": MetricSample(600.0, NOW)}
        )
        bundler = EvidenceBundler(metrics, traces=_FakeTraces(("trace-1",)), now_fn=lambda: NOW)
        bundle = bundler.check_regression(_SPEC)
        assert bundle is not None
        assert any(f.source == "jaeger" for f in bundle.verified_facts)

    def test_tenant_scoped_log_query_includes_tenant_id(self) -> None:
        metrics = _FakeMetrics(
            {"tts_p95_current": MetricSample(1000.0, NOW), "tts_p95_baseline": MetricSample(600.0, NOW)}
        )
        captured_query: dict[str, str] = {}

        class _CapturingLogs:
            def query(self, logql: str, *, limit: int = 20) -> tuple[str, ...]:
                captured_query["q"] = logql
                return ("err",)

        bundler = EvidenceBundler(metrics, logs=_CapturingLogs(), now_fn=lambda: NOW)
        bundler.check_regression(_SPEC, tenant_id="tenant-9")
        assert 'tenant_id="tenant-9"' in captured_query["q"]
