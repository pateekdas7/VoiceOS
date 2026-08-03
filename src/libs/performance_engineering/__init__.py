"""Performance Engineering library — ContinuousProfiler, BenchmarkSuite,
RegressionDetector, OptimizationPlaybook (V3 Ch19).

Architecture: V3 Ch19 (Performance Engineering).
"""

from __future__ import annotations

from src.libs.performance_engineering.benchmarks import (
    BenchmarkReport,
    BenchmarkSuite,
    FixtureTimingSource,
    StageResult,
    StageTimingSource,
)
from src.libs.performance_engineering.optimization import OptimizationAction, OptimizationPlaybook
from src.libs.performance_engineering.profiler import (
    BaselineRepository,
    ContinuousProfiler,
    InMemoryBaselineRepository,
    TimingResult,
)
from src.libs.performance_engineering.regression_gate import (
    RegressionDetector,
    RegressionResult,
    StageRegression,
)

__all__ = [
    "BaselineRepository",
    "BenchmarkReport",
    "BenchmarkSuite",
    "ContinuousProfiler",
    "FixtureTimingSource",
    "InMemoryBaselineRepository",
    "OptimizationAction",
    "OptimizationPlaybook",
    "RegressionDetector",
    "RegressionResult",
    "StageRegression",
    "StageResult",
    "StageTimingSource",
    "TimingResult",
]
