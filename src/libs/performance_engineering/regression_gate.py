"""RegressionDetector -- CI gate: fails if any stage p95 exceeds its baseline by >10% (V3 Ch19, Sprint-028).

Prevents performance regressions from merging silently. Compares a
current :class:`~.benchmarks.BenchmarkReport`'s per-stage p95 measurements
against a set of registered baselines (typically yesterday's
``performance_baselines`` row per stage, via
:meth:`RegressionDetector.from_benchmark_report` seeded from a prior
:class:`~.benchmarks.BenchmarkSuite` run). ``scripts/check_performance_regression.py``
wires this into ``.github/workflows/ci.yml`` as a blocking gate.

Architecture: V3 Ch19 (Performance Engineering -- regression-detection gates).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .benchmarks import BenchmarkReport

REGRESSION_THRESHOLD = 0.10
"""A stage regresses if its measured p95 exceeds its registered baseline by more than this fraction."""


@dataclass(frozen=True)
class RegressionResult:
    """One stage's regression check outcome."""

    stage_name: str
    baseline_p95_ms: float
    measured_p95_ms: float

    @property
    def regression_pct(self) -> float:
        """Fractional change vs. baseline (0.15 == +15%). 0.0 if the baseline itself is non-positive."""
        if self.baseline_p95_ms <= 0:
            return 0.0
        return (self.measured_p95_ms - self.baseline_p95_ms) / self.baseline_p95_ms

    @property
    def is_regression(self) -> bool:
        return self.regression_pct > REGRESSION_THRESHOLD


@dataclass(frozen=True)
class RegressionReport:
    """Output of :meth:`RegressionDetector.check` -- every stage compared against its baseline."""

    results: tuple[RegressionResult, ...]

    @property
    def passed(self) -> bool:
        """``True`` iff no stage regressed by more than :data:`REGRESSION_THRESHOLD`."""
        return not any(r.is_regression for r in self.results)

    def regressions(self) -> tuple[RegressionResult, ...]:
        return tuple(r for r in self.results if r.is_regression)


class RegressionDetector:
    """CI gate: fails if any stage's measured p95 is more than 10% above its registered baseline."""

    def __init__(self, baselines: Mapping[str, float]) -> None:
        self._baselines = dict(baselines)

    @classmethod
    def from_benchmark_report(cls, baseline_report: BenchmarkReport) -> RegressionDetector:
        """Build a detector from a prior :class:`~.benchmarks.BenchmarkReport` (e.g. yesterday's baseline run)."""
        return cls({r.stage_name: r.p95_ms for r in baseline_report.results})

    def check(self, current_report: BenchmarkReport) -> RegressionReport:
        """Compare ``current_report``'s per-stage p95 against this detector's registered baselines.

        Stages present in ``current_report`` with no registered baseline
        are skipped (nothing to regress against yet -- a new stage's
        first benchmark run establishes its baseline, it cannot fail
        against itself).
        """
        results: list[RegressionResult] = []
        for stage_result in current_report.results:
            baseline = self._baselines.get(stage_result.stage_name)
            if baseline is None:
                continue
            results.append(
                RegressionResult(
                    stage_name=stage_result.stage_name,
                    baseline_p95_ms=baseline,
                    measured_p95_ms=stage_result.p95_ms,
                )
            )
        return RegressionReport(results=tuple(results))


__all__ = [
    "REGRESSION_THRESHOLD",
    "RegressionDetector",
    "RegressionReport",
    "RegressionResult",
]
