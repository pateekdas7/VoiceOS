"""Unit tests: src/libs/performance_engineering/ (ContinuousProfiler, BenchmarkSuite, RegressionDetector,
OptimizationPlaybook) -- Sprint-028 Phase 1 (V3 Ch19).

Required per Sprint-028.md's own validation table: "BenchmarkSuite passes
for fixture data; RegressionDetector fails on simulated 15% regression."
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import pytest
from scripts.check_performance_regression import (
    default_current_fixture,
)
from scripts.check_performance_regression import (
    evaluate as check_regression_evaluate,
)
from scripts.check_performance_regression import (
    main as check_regression_main,
)

from src.libs.performance_engineering import (
    STAGE_BUDGETS_MS,
    BenchmarkReport,
    BenchmarkSuite,
    ContinuousProfiler,
    InMemoryBaselineStore,
    OptimizationPlaybook,
    RegressionDetector,
    StageBenchmarkResult,
    StagePercentiles,
    StaticFixtureProvider,
    compute_percentile,
)

# ---------------------------------------------------------------------------
# compute_percentile
# ---------------------------------------------------------------------------


class TestComputePercentile:
    def test_single_sample(self) -> None:
        assert compute_percentile([42.0], 95) == 42.0

    def test_p50_is_median_like(self) -> None:
        samples = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert compute_percentile(samples, 50) == 30.0

    def test_p95_of_ordered_samples(self) -> None:
        samples = [float(i) for i in range(1, 101)]  # 1..100
        p95 = compute_percentile(samples, 95)
        assert 94.0 <= p95 <= 96.0

    def test_unordered_input_is_sorted_internally(self) -> None:
        assert compute_percentile([30.0, 10.0, 20.0], 50) == 20.0

    def test_empty_samples_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one sample"):
            compute_percentile([], 95)

    def test_out_of_range_percentile_raises(self) -> None:
        with pytest.raises(ValueError, match="0, 100"):
            compute_percentile([1.0], 150)


# ---------------------------------------------------------------------------
# ContinuousProfiler
# ---------------------------------------------------------------------------


class TestContinuousProfiler:
    def test_profile_stage_captures_duration_and_return_value(self) -> None:
        profiler = ContinuousProfiler(clock=_FakeClock([0.0, 0.05]))

        timing = profiler.profile_stage("stt", lambda: "transcript")

        assert timing.stage_name == "stt"
        assert timing.duration_ms == pytest.approx(50.0)
        assert timing.result == "transcript"

    def test_profile_stage_accumulates_samples(self) -> None:
        clock = _FakeClock([0.0, 0.01, 0.02, 0.05])
        profiler = ContinuousProfiler(clock=clock)

        profiler.profile_stage("stt", lambda: None)
        profiler.profile_stage("stt", lambda: None)

        assert profiler.samples_for("stt") == pytest.approx((10.0, 30.0))

    def test_record_sample_for_externally_measured_stage(self) -> None:
        profiler = ContinuousProfiler()
        profiler.record_sample("tts_first_clause", 123.4)
        profiler.record_sample("tts_first_clause", 456.7)

        assert profiler.samples_for("tts_first_clause") == (123.4, 456.7)

    def test_flush_daily_percentiles_persists_to_baseline_store(self) -> None:
        store = InMemoryBaselineStore()
        profiler = ContinuousProfiler(baseline_store=store)
        for duration in [100.0, 200.0, 300.0, 400.0, 500.0]:
            profiler.record_sample("stt", duration)

        today = date(2026, 7, 9)
        flushed = profiler.flush_daily_percentiles(recorded_date=today)

        assert len(flushed) == 1
        percentiles = flushed[0]
        assert percentiles.stage_name == "stt"
        assert percentiles.sample_count == 5
        assert percentiles.p50_ms == 300.0

        stored = store.percentiles_for("stt", today)
        assert stored is not None
        assert stored == percentiles

    def test_flush_with_no_samples_is_a_noop(self) -> None:
        profiler = ContinuousProfiler()
        assert profiler.flush_daily_percentiles() == ()

    def test_reset_clears_samples(self) -> None:
        profiler = ContinuousProfiler()
        profiler.record_sample("stt", 100.0)
        profiler.reset()
        assert profiler.samples_for("stt") == ()

    def test_real_clock_default_produces_nonnegative_duration(self) -> None:
        profiler = ContinuousProfiler()
        timing = profiler.profile_stage("vad", lambda: time.sleep(0))
        assert timing.duration_ms >= 0.0


class _FakeClock:
    """Deterministic ``time.perf_counter``-compatible stand-in for latency assertions."""

    def __init__(self, ticks: list[float]) -> None:
        self._ticks = iter(ticks)

    def __call__(self) -> float:
        return next(self._ticks)


# ---------------------------------------------------------------------------
# InMemoryBaselineStore
# ---------------------------------------------------------------------------


class TestInMemoryBaselineStore:
    def test_percentiles_for_unknown_stage_returns_none(self) -> None:
        store = InMemoryBaselineStore()
        assert store.percentiles_for("stt", date(2026, 7, 9)) is None

    def test_record_and_retrieve_round_trip(self) -> None:
        store = InMemoryBaselineStore()
        percentiles = StagePercentiles(
            stage_name="llm_ttft",
            recorded_date=date(2026, 7, 9),
            p50_ms=200.0,
            p95_ms=340.0,
            p99_ms=400.0,
            sample_count=100,
        )
        store.record_percentiles(percentiles)
        assert store.percentiles_for("llm_ttft", date(2026, 7, 9)) == percentiles


# ---------------------------------------------------------------------------
# BenchmarkSuite
# ---------------------------------------------------------------------------


class TestBenchmarkSuite:
    def test_run_benchmarks_passes_for_fixture_data_within_budget(self) -> None:
        """Required named check: 'BenchmarkSuite passes for fixture data' (Sprint-028.md Phase 1 table)."""
        fixtures = {
            "stt": tuple(float(x) for x in range(200, 300)),  # p95 ~295ms, budget 300ms
            "cil": tuple(float(x) for x in range(50, 115)),  # p95 ~114ms, budget 120ms
            "llm_ttft": tuple(float(x) for x in range(250, 345)),  # p95 ~340ms, budget 350ms
            "tts_first_clause": tuple(float(x) for x in range(150, 245)),  # p95 ~240ms, budget 250ms
        }
        suite = BenchmarkSuite(StaticFixtureProvider(fixtures))

        report = suite.run_benchmarks("ci")

        assert report.environment == "ci"
        assert len(report.results) == 4
        assert report.passed is True
        assert report.failures() == ()

    def test_run_benchmarks_fails_stage_exceeding_budget(self) -> None:
        fixtures = {"stt": tuple(float(x) for x in range(280, 400))}  # p95 far above 300ms budget
        suite = BenchmarkSuite(StaticFixtureProvider(fixtures))

        report = suite.run_benchmarks("staging")

        assert report.passed is False
        failures = report.failures()
        assert len(failures) == 1
        assert failures[0].stage_name == "stt"

    def test_stages_with_no_fixture_data_are_skipped(self) -> None:
        suite = BenchmarkSuite(StaticFixtureProvider({}))
        report = suite.run_benchmarks("ci")
        assert report.results == ()
        assert report.passed is True  # vacuously true — nothing benchmarked, nothing failed

    def test_stage_benchmark_result_within_budget_property(self) -> None:
        within = StageBenchmarkResult(stage_name="stt", p95_ms=250.0, budget_ms=300.0, sample_count=10)
        over = StageBenchmarkResult(stage_name="stt", p95_ms=350.0, budget_ms=300.0, sample_count=10)
        assert within.within_budget is True
        assert over.within_budget is False

    def test_custom_budgets_override_default(self) -> None:
        suite = BenchmarkSuite(
            StaticFixtureProvider({"stt": (50.0, 60.0, 70.0)}),
            budgets={"stt": 40.0},
        )
        report = suite.run_benchmarks("ci")
        assert report.results[0].within_budget is False


# ---------------------------------------------------------------------------
# RegressionDetector
# ---------------------------------------------------------------------------


class TestRegressionDetector:
    def test_no_regression_when_within_threshold(self) -> None:
        detector = RegressionDetector({"stt": 300.0})
        current = BenchmarkReport(
            environment="staging",
            results=(StageBenchmarkResult(stage_name="stt", p95_ms=320.0, budget_ms=300.0, sample_count=50),),
        )  # +6.7%, under the 10% threshold

        report = detector.check(current)

        assert report.passed is True
        assert report.regressions() == ()

    def test_fails_on_simulated_15_percent_regression(self) -> None:
        """Required named check: 'RegressionDetector fails on simulated 15% regression' (Sprint-028.md Phase 1 table)."""
        detector = RegressionDetector({"stt": 300.0})
        current = BenchmarkReport(
            environment="staging",
            results=(StageBenchmarkResult(stage_name="stt", p95_ms=345.0, budget_ms=300.0, sample_count=50),),
        )  # 300 * 1.15 = 345 -- exactly a 15% regression

        report = detector.check(current)

        assert report.passed is False
        regressions = report.regressions()
        assert len(regressions) == 1
        assert regressions[0].stage_name == "stt"
        assert regressions[0].regression_pct == pytest.approx(0.15)
        assert regressions[0].is_regression is True

    def test_exactly_at_threshold_is_not_a_regression(self) -> None:
        detector = RegressionDetector({"stt": 300.0})
        current = BenchmarkReport(
            environment="staging",
            results=(StageBenchmarkResult(stage_name="stt", p95_ms=330.0, budget_ms=300.0, sample_count=50),),
        )  # exactly +10% -- strictly greater-than is required to fail

        report = detector.check(current)

        assert report.passed is True

    def test_stage_with_no_registered_baseline_is_skipped(self) -> None:
        detector = RegressionDetector({})
        current = BenchmarkReport(
            environment="staging",
            results=(StageBenchmarkResult(stage_name="stt", p95_ms=1000.0, budget_ms=300.0, sample_count=1),),
        )

        report = detector.check(current)

        assert report.results == ()
        assert report.passed is True

    def test_from_benchmark_report_seeds_baselines(self) -> None:
        baseline_report = BenchmarkReport(
            environment="staging",
            results=(StageBenchmarkResult(stage_name="stt", p95_ms=280.0, budget_ms=300.0, sample_count=100),),
        )
        detector = RegressionDetector.from_benchmark_report(baseline_report)

        current = BenchmarkReport(
            environment="staging",
            results=(StageBenchmarkResult(stage_name="stt", p95_ms=290.0, budget_ms=300.0, sample_count=100),),
        )
        report = detector.check(current)

        assert report.results[0].baseline_p95_ms == 280.0
        assert report.passed is True

    def test_nonpositive_baseline_never_reports_a_regression(self) -> None:
        detector = RegressionDetector({"stt": 0.0})
        current = BenchmarkReport(
            environment="staging",
            results=(StageBenchmarkResult(stage_name="stt", p95_ms=999.0, budget_ms=300.0, sample_count=1),),
        )
        report = detector.check(current)
        assert report.results[0].regression_pct == 0.0
        assert report.results[0].is_regression is False


# ---------------------------------------------------------------------------
# OptimizationPlaybook
# ---------------------------------------------------------------------------


class TestOptimizationPlaybook:
    def test_procedure_for_known_stage(self) -> None:
        playbook = OptimizationPlaybook()
        procedure = playbook.procedure_for("tts_first_clause")
        assert procedure is not None
        assert procedure.stage_name == "tts_first_clause"
        assert len(procedure.likely_causes) > 0
        assert len(procedure.procedure) > 0

    def test_procedure_for_unknown_stage_returns_none(self) -> None:
        playbook = OptimizationPlaybook()
        assert playbook.procedure_for("nonexistent_stage") is None

    def test_all_procedures_cover_every_budgeted_stage(self) -> None:
        playbook = OptimizationPlaybook()
        covered = {p.stage_name for p in playbook.all_procedures()}
        for stage_name in STAGE_BUDGETS_MS:
            assert stage_name in covered

    def test_stt_procedure_references_known_technical_debt(self) -> None:
        playbook = OptimizationPlaybook()
        procedure = playbook.procedure_for("stt")
        assert procedure is not None
        assert "TT-010" in procedure.related_technical_debt

    def test_tts_procedure_references_ttfa_technical_debt(self) -> None:
        playbook = OptimizationPlaybook()
        procedure = playbook.procedure_for("tts_first_clause")
        assert procedure is not None
        assert "TT-001-residual" in procedure.related_technical_debt


# ---------------------------------------------------------------------------
# scripts/check_performance_regression.py (CI gate wiring, Sprint-028 AC)
# ---------------------------------------------------------------------------


class TestCheckPerformanceRegressionScript:
    def test_default_current_fixture_passes(self) -> None:
        """Required: the CI gate is green in the default (no-regression) case."""
        assert check_regression_main([]) == 0

    def test_simulate_regression_flag_fails(self) -> None:
        """Required AC: 'RegressionDetector CI gate ... fails correctly on simulated regression'."""
        assert check_regression_main(["--simulate-regression"]) == 1

    def test_custom_current_file_is_honored(self, tmp_path: object) -> None:
        import json
        from pathlib import Path

        current_path = Path(str(tmp_path)) / "current.json"
        current_path.write_text(json.dumps({"stt": 285.0}), encoding="utf-8")

        assert check_regression_main(["--current", str(current_path)]) == 0

    def test_default_current_fixture_is_under_every_budget(self) -> None:
        fixture = default_current_fixture()
        for stage, p95 in fixture.items():
            assert p95 < STAGE_BUDGETS_MS[stage]

    def test_evaluate_helper_flags_injected_regression(self) -> None:
        current = default_current_fixture()
        stage = next(iter(current))
        current[stage] = STAGE_BUDGETS_MS[stage] * 1.20

        report = check_regression_evaluate(current)

        assert report.passed is False
        assert any(r.stage_name == stage for r in report.regressions())


# ---------------------------------------------------------------------------
# Cross-module sanity: percentile computation matches Sprint-028's own budgets table
# ---------------------------------------------------------------------------


def test_stage_budgets_match_v1_ch23_literal_values() -> None:
    assert STAGE_BUDGETS_MS["stt"] == 300.0
    assert STAGE_BUDGETS_MS["cil"] == 120.0
    assert STAGE_BUDGETS_MS["llm_ttft"] == 350.0
    assert STAGE_BUDGETS_MS["tts_first_clause"] == 250.0


def test_stage_percentiles_is_stable_across_recorded_dates() -> None:
    day_one = date(2026, 7, 9)
    day_two = day_one + timedelta(days=1)
    store = InMemoryBaselineStore()
    store.record_percentiles(
        StagePercentiles(
            stage_name="stt", recorded_date=day_one, p50_ms=200.0, p95_ms=280.0, p99_ms=300.0, sample_count=10
        )
    )
    store.record_percentiles(
        StagePercentiles(
            stage_name="stt", recorded_date=day_two, p50_ms=210.0, p95_ms=290.0, p99_ms=310.0, sample_count=10
        )
    )
    assert store.percentiles_for("stt", day_one).p95_ms == 280.0  # type: ignore[union-attr]
    assert store.percentiles_for("stt", day_two).p95_ms == 290.0  # type: ignore[union-attr]
