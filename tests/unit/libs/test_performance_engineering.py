"""Unit tests for ContinuousProfiler, BenchmarkSuite, RegressionDetector,
OptimizationPlaybook (V3 Ch19 — Sprint-028 Phase 1).

All tests use fixture/in-memory backends — no real GPU or Postgres required.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime

import pytest

from src.libs.performance_engineering.benchmarks import (
    STAGE_BUDGETS_MS,
    BenchmarkReport,
    BenchmarkSuite,
    FixtureTimingSource,
    RegressionFixtureTimingSource,
    StageResult,
)
from src.libs.performance_engineering.optimization import OptimizationPlaybook
from src.libs.performance_engineering.profiler import (
    ContinuousProfiler,
    InMemoryBaselineRepository,
)
from src.libs.performance_engineering.regression_gate import (
    REGRESSION_THRESHOLD_FRACTION,
    RegressionDetector,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store_with_baseline(stage: str, p95_ms: float) -> InMemoryBaselineRepository:
    store = InMemoryBaselineRepository()
    store.record(stage, p50_ms=p95_ms * 0.85, p95_ms=p95_ms, p99_ms=p95_ms * 1.05, measured_date=date.today())
    return store


def _make_report_with_p95(stage_overrides: dict[str, float]) -> BenchmarkReport:
    """Build a BenchmarkReport with custom p95 values for specified stages."""
    stages = []
    for stage, budget in STAGE_BUDGETS_MS.items():
        p95 = stage_overrides.get(stage, budget * 0.90)
        stages.append(
            StageResult(
                stage=stage,
                p50_ms=p95 * 0.85,
                p95_ms=p95,
                p99_ms=p95 * 1.05,
                budget_ms=budget,
            )
        )
    return BenchmarkReport(
        environment="test",
        timestamp=datetime.now(tz=UTC),
        stages=tuple(stages),
    )


# ---------------------------------------------------------------------------
# ContinuousProfiler
# ---------------------------------------------------------------------------


class TestContinuousProfiler:
    def test_profile_stage_returns_function_value(self) -> None:
        store = InMemoryBaselineRepository()
        profiler = ContinuousProfiler(store)
        result = profiler.profile_stage("stt", lambda: 42)
        assert result.value == 42

    def test_profile_stage_stage_name_in_result(self) -> None:
        store = InMemoryBaselineRepository()
        profiler = ContinuousProfiler(store)
        result = profiler.profile_stage("llm_ttft", lambda: "token")
        assert result.stage == "llm_ttft"

    def test_profile_stage_measures_positive_duration(self) -> None:
        store = InMemoryBaselineRepository()
        profiler = ContinuousProfiler(store)
        result = profiler.profile_stage("tts_first_clause", lambda: None)
        assert result.duration_ms >= 0.0

    def test_profile_stage_measures_sleep_duration(self) -> None:
        store = InMemoryBaselineRepository()
        profiler = ContinuousProfiler(store)

        def slow_fn() -> str:
            time.sleep(0.02)
            return "done"

        result = profiler.profile_stage("stt", slow_fn)
        assert result.duration_ms >= 10.0  # at least half of 20ms sleep

    def test_profile_stage_timestamp_is_utc(self) -> None:
        store = InMemoryBaselineRepository()
        profiler = ContinuousProfiler(store)
        before = datetime.now(tz=UTC)
        result = profiler.profile_stage("cil", lambda: None)
        after = datetime.now(tz=UTC)
        assert before <= result.timestamp <= after

    def test_sample_count_increments_per_call(self) -> None:
        store = InMemoryBaselineRepository()
        profiler = ContinuousProfiler(store)
        assert profiler.sample_count("stt") == 0
        profiler.profile_stage("stt", lambda: None)
        profiler.profile_stage("stt", lambda: None)
        assert profiler.sample_count("stt") == 2

    def test_flush_persists_p95_to_store(self) -> None:
        store = InMemoryBaselineRepository()
        profiler = ContinuousProfiler(store)
        for _ in range(10):
            profiler.profile_stage("stt", lambda: None)
        profiler.flush()
        p95 = store.fetch_latest_p95("stt")
        assert p95 is not None
        assert p95 >= 0.0

    def test_flush_clears_samples(self) -> None:
        store = InMemoryBaselineRepository()
        profiler = ContinuousProfiler(store)
        profiler.profile_stage("stt", lambda: None)
        profiler.flush()
        assert profiler.sample_count("stt") == 0

    def test_flush_multiple_stages(self) -> None:
        store = InMemoryBaselineRepository()
        profiler = ContinuousProfiler(store)
        for stage in ["stt", "llm_ttft", "tts_first_clause"]:
            profiler.profile_stage(stage, lambda: None)
        profiler.flush()
        for stage in ["stt", "llm_ttft", "tts_first_clause"]:
            assert store.fetch_latest_p95(stage) is not None

    def test_flush_empty_samples_is_noop(self) -> None:
        store = InMemoryBaselineRepository()
        profiler = ContinuousProfiler(store)
        profiler.flush()
        assert store.fetch_latest_p95("stt") is None


# ---------------------------------------------------------------------------
# InMemoryBaselineRepository
# ---------------------------------------------------------------------------


class TestInMemoryBaselineRepository:
    def test_returns_none_for_unknown_stage(self) -> None:
        store = InMemoryBaselineRepository()
        assert store.fetch_latest_p95("nonexistent") is None

    def test_record_and_fetch_roundtrip(self) -> None:
        store = InMemoryBaselineRepository()
        store.record("stt", p50_ms=250.0, p95_ms=285.0, p99_ms=295.0, measured_date=date(2026, 8, 1))
        assert store.fetch_latest_p95("stt") == pytest.approx(285.0)

    def test_fetch_returns_latest_date(self) -> None:
        store = InMemoryBaselineRepository()
        store.record("stt", p50_ms=250.0, p95_ms=280.0, p99_ms=290.0, measured_date=date(2026, 7, 1))
        store.record("stt", p50_ms=255.0, p95_ms=283.0, p99_ms=293.0, measured_date=date(2026, 8, 1))
        assert store.fetch_latest_p95("stt") == pytest.approx(283.0)

    def test_stages_are_isolated(self) -> None:
        store = InMemoryBaselineRepository()
        store.record("stt", p50_ms=250.0, p95_ms=280.0, p99_ms=290.0, measured_date=date.today())
        store.record("llm_ttft", p50_ms=300.0, p95_ms=340.0, p99_ms=350.0, measured_date=date.today())
        assert store.fetch_latest_p95("stt") == pytest.approx(280.0)
        assert store.fetch_latest_p95("llm_ttft") == pytest.approx(340.0)


# ---------------------------------------------------------------------------
# BenchmarkSuite
# ---------------------------------------------------------------------------


class TestBenchmarkSuite:
    def test_all_stages_pass_with_fixture_timings(self) -> None:
        suite = BenchmarkSuite(source=FixtureTimingSource(), n_samples=10)
        report = suite.run_benchmarks("ci")
        assert report.passed, (
            f"Expected all stages to pass with fixture timings; "
            f"failing: {[s.stage for s in report.stages if not s.passed]}"
        )

    def test_report_contains_all_expected_stages(self) -> None:
        suite = BenchmarkSuite(source=FixtureTimingSource())
        report = suite.run_benchmarks("ci")
        stage_names = {s.stage for s in report.stages}
        assert stage_names == set(STAGE_BUDGETS_MS)

    def test_p95_within_budget_for_all_fixture_stages(self) -> None:
        suite = BenchmarkSuite(source=FixtureTimingSource())
        report = suite.run_benchmarks("ci")
        for stage_result in report.stages:
            assert stage_result.p95_ms <= stage_result.budget_ms, (
                f"Stage {stage_result.stage}: p95={stage_result.p95_ms:.1f} > budget={stage_result.budget_ms:.1f}"
            )

    def test_stage_over_budget_fails_report(self) -> None:
        suite = BenchmarkSuite(source=RegressionFixtureTimingSource())
        report = suite.run_benchmarks("regression-test")
        # STT fixtures are scaled by 1.15x — STT p95 should exceed 300ms budget
        stt = report.stage("stt")
        assert stt is not None
        assert not stt.passed

    def test_report_passed_false_when_any_stage_over_budget(self) -> None:
        suite = BenchmarkSuite(source=RegressionFixtureTimingSource())
        report = suite.run_benchmarks("regression-test")
        assert not report.passed

    def test_stage_result_lookup_by_name(self) -> None:
        suite = BenchmarkSuite(source=FixtureTimingSource())
        report = suite.run_benchmarks("ci")
        assert report.stage("stt") is not None
        assert report.stage("nonexistent") is None

    def test_report_timestamp_is_recent(self) -> None:
        before = datetime.now(tz=UTC)
        suite = BenchmarkSuite(source=FixtureTimingSource())
        report = suite.run_benchmarks("ci")
        after = datetime.now(tz=UTC)
        assert before <= report.timestamp <= after

    def test_stage_result_overage_pct_positive_when_over_budget(self) -> None:
        result = StageResult(stage="stt", p50_ms=290.0, p95_ms=345.0, p99_ms=360.0, budget_ms=300.0)
        assert result.overage_pct > 0.0

    def test_stage_result_overage_pct_negative_when_under_budget(self) -> None:
        result = StageResult(stage="stt", p50_ms=200.0, p95_ms=270.0, p99_ms=295.0, budget_ms=300.0)
        assert result.overage_pct < 0.0


# ---------------------------------------------------------------------------
# RegressionDetector
# ---------------------------------------------------------------------------


class TestRegressionDetector:
    def test_no_baseline_skips_stage(self) -> None:
        """When no baseline is stored, the detector skips the stage and passes."""
        store = InMemoryBaselineRepository()
        detector = RegressionDetector(store)
        report = _make_report_with_p95({"stt": 320.0})
        result = detector.check(report)
        assert result.passed
        assert len(result.regressions) == 0

    def test_within_threshold_passes(self) -> None:
        """5 % above baseline is within the 10 % threshold — should pass."""
        store = _make_store_with_baseline("stt", p95_ms=280.0)
        for stage in ["cil", "llm_ttft", "tts_first_clause"]:
            store.record(stage, 80.0, STAGE_BUDGETS_MS[stage] * 0.90, 95.0, date.today())
        detector = RegressionDetector(store)
        # 280 * 1.05 = 294 < 280 * 1.10 = 308 -> passes
        report = _make_report_with_p95({"stt": 280.0 * 1.05})
        result = detector.check(report)
        stt_reg = next((r for r in result.regressions if r.stage == "stt"), None)
        assert stt_reg is not None
        assert not stt_reg.exceeded
        assert result.passed

    def test_exactly_at_threshold_passes(self) -> None:
        """Exactly 10 % above baseline is at the limit — should pass (≤ threshold)."""
        baseline = 280.0
        store = _make_store_with_baseline("stt", p95_ms=baseline)
        for stage in ["cil", "llm_ttft", "tts_first_clause"]:
            store.record(stage, 80.0, STAGE_BUDGETS_MS[stage] * 0.90, 95.0, date.today())
        detector = RegressionDetector(store)
        exact_threshold = baseline * (1.0 + REGRESSION_THRESHOLD_FRACTION)
        report = _make_report_with_p95({"stt": exact_threshold})
        result = detector.check(report)
        stt_reg = next((r for r in result.regressions if r.stage == "stt"), None)
        assert stt_reg is not None
        assert not stt_reg.exceeded

    def test_15pct_regression_fails_ci(self) -> None:
        """Sprint-028 required: STT p95 at 115 % of baseline -> CI gate FAILS."""
        baseline_stt_p95 = 280.0
        store = _make_store_with_baseline("stt", p95_ms=baseline_stt_p95)
        for stage in ["cil", "llm_ttft", "tts_first_clause"]:
            store.record(stage, 80.0, STAGE_BUDGETS_MS[stage] * 0.90, 95.0, date.today())
        detector = RegressionDetector(store)
        # 280 * 1.15 = 322 > 280 * 1.10 = 308 -> regression detected
        regressed_p95 = baseline_stt_p95 * 1.15
        report = _make_report_with_p95({"stt": regressed_p95})
        result = detector.check(report)
        assert not result.passed, "15% regression above baseline must fail the CI gate"
        failed = result.failed_stages
        assert len(failed) == 1
        assert failed[0].stage == "stt"
        assert failed[0].overage_pct == pytest.approx(15.0, abs=0.01)

    def test_regression_fixture_source_triggers_gate(self) -> None:
        """End-to-end: RegressionFixtureTimingSource + stored baseline -> gate fails."""
        # Populate baselines from FixtureTimingSource (represents yesterday's good run)
        good_suite = BenchmarkSuite(source=FixtureTimingSource())
        good_report = good_suite.run_benchmarks("baseline-run")
        store = InMemoryBaselineRepository()
        today = date.today()
        for stage_result in good_report.stages:
            store.record(
                stage_result.stage,
                p50_ms=stage_result.p50_ms,
                p95_ms=stage_result.p95_ms,
                p99_ms=stage_result.p99_ms,
                measured_date=today,
            )

        # Run with regressed source (STT x 1.15)
        regressed_suite = BenchmarkSuite(source=RegressionFixtureTimingSource())
        regressed_report = regressed_suite.run_benchmarks("regressed-run")

        detector = RegressionDetector(store)
        result = detector.check(regressed_report)
        assert not result.passed
        failed_stages = [r.stage for r in result.failed_stages]
        assert "stt" in failed_stages

    def test_multiple_regressions_all_reported(self) -> None:
        store = InMemoryBaselineRepository()
        today = date.today()
        for stage in STAGE_BUDGETS_MS:
            store.record(stage, 80.0, STAGE_BUDGETS_MS[stage] * 0.90, 95.0, today)
        detector = RegressionDetector(store)
        # Set all stages 20 % above baseline
        overrides = {stage: STAGE_BUDGETS_MS[stage] * 0.90 * 1.20 for stage in STAGE_BUDGETS_MS}
        report = _make_report_with_p95(overrides)
        result = detector.check(report)
        assert not result.passed
        assert len(result.failed_stages) == len(STAGE_BUDGETS_MS)


# ---------------------------------------------------------------------------
# OptimizationPlaybook
# ---------------------------------------------------------------------------


class TestOptimizationPlaybook:
    def test_suggest_returns_actions_for_stt(self) -> None:
        actions = OptimizationPlaybook.suggest("stt", observed_p95_ms=380.0)
        assert len(actions) > 0
        assert all(a.stage == "stt" for a in actions)

    def test_suggest_returns_actions_for_llm_ttft(self) -> None:
        actions = OptimizationPlaybook.suggest("llm_ttft", observed_p95_ms=420.0)
        assert len(actions) > 0

    def test_suggest_returns_actions_for_cil(self) -> None:
        actions = OptimizationPlaybook.suggest("cil", observed_p95_ms=150.0)
        assert len(actions) > 0

    def test_suggest_returns_actions_for_tts(self) -> None:
        actions = OptimizationPlaybook.suggest("tts_first_clause", observed_p95_ms=310.0)
        assert len(actions) > 0

    def test_unknown_stage_returns_empty(self) -> None:
        actions = OptimizationPlaybook.suggest("nonexistent_stage", observed_p95_ms=500.0)
        assert actions == []

    def test_actions_sorted_by_expected_improvement_descending(self) -> None:
        actions = OptimizationPlaybook.suggest("stt", observed_p95_ms=380.0)
        import itertools

        for a, b in itertools.pairwise(actions):
            assert a.expected_improvement_pct >= b.expected_improvement_pct

    def test_all_stages_covered(self) -> None:
        covered = set(OptimizationPlaybook.all_stages())
        assert set(STAGE_BUDGETS_MS).issubset(covered)

    def test_action_descriptions_are_nonempty(self) -> None:
        for stage in OptimizationPlaybook.all_stages():
            for action in OptimizationPlaybook.suggest(stage, 999.0):
                assert action.description.strip()

    def test_custom_budget_overrides_default(self) -> None:
        actions = OptimizationPlaybook.suggest("stt", observed_p95_ms=500.0, budget_ms=200.0)
        assert len(actions) > 0
