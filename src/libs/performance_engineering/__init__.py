"""Performance Engineering -- profiling harness, benchmark suite, regression gate, optimization playbook (V3 Ch19).

Sprint-028.md's literal path is ``src/libs/performance-engineering/`` --
not a valid Python package name (hyphens are not permitted in module
identifiers). Renamed to ``performance_engineering`` following the same
dashed-path-to-underscore deviation precedent as ``monitoring/gpu_fleet``
(Sprint-027), ``src/services/cost_optimizer`` (Sprint-027), and
``src/services/ops_analytics`` (Sprint-027).

Architecture: V3 Ch19 (Performance Engineering).
"""

from __future__ import annotations

from src.libs.performance_engineering.benchmarks import (
    STAGE_BUDGETS_MS,
    BenchmarkReport,
    BenchmarkSuite,
    StageBenchmarkResult,
    StageFixtureProvider,
    StaticFixtureProvider,
)
from src.libs.performance_engineering.optimization import OptimizationPlaybook, OptimizationProcedure
from src.libs.performance_engineering.profiler import (
    BaselineStore,
    ContinuousProfiler,
    InMemoryBaselineStore,
    StagePercentiles,
    TimingResult,
    compute_percentile,
)
from src.libs.performance_engineering.regression_gate import (
    REGRESSION_THRESHOLD,
    RegressionDetector,
    RegressionReport,
    RegressionResult,
)

__all__ = [
    "REGRESSION_THRESHOLD",
    "STAGE_BUDGETS_MS",
    "BaselineStore",
    "BenchmarkReport",
    "BenchmarkSuite",
    "ContinuousProfiler",
    "InMemoryBaselineStore",
    "OptimizationPlaybook",
    "OptimizationProcedure",
    "RegressionDetector",
    "RegressionReport",
    "RegressionResult",
    "StageBenchmarkResult",
    "StageFixtureProvider",
    "StagePercentiles",
    "StaticFixtureProvider",
    "TimingResult",
    "compute_percentile",
]
