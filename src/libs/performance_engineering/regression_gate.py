"""RegressionDetector — CI gate that fails if any stage p95 regresses > 10% above baseline (V3 Ch19).

Architecture: V3 Ch19 (Performance Engineering).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.libs.performance_engineering.benchmarks import BenchmarkReport
from src.libs.performance_engineering.profiler import BaselineRepository

REGRESSION_THRESHOLD_FRACTION = 0.10
"""Fraction above baseline p95 that triggers a regression (10 %)."""


@dataclass(frozen=True)
class StageRegression:
    """Regression record for one pipeline stage."""

    stage: str
    baseline_p95_ms: float
    current_p95_ms: float

    @property
    def threshold_ms(self) -> float:
        """Maximum acceptable p95 — baseline x (1 + threshold fraction)."""
        return self.baseline_p95_ms * (1.0 + REGRESSION_THRESHOLD_FRACTION)

    @property
    def exceeded(self) -> bool:
        """True if current p95 is above the allowed threshold."""
        return self.current_p95_ms > self.threshold_ms

    @property
    def overage_pct(self) -> float:
        """How far above baseline p95 the current result is (percentage)."""
        return (self.current_p95_ms - self.baseline_p95_ms) / self.baseline_p95_ms * 100.0


@dataclass(frozen=True)
class RegressionResult:
    """Outcome of one regression check across all benchmarked stages."""

    regressions: tuple[StageRegression, ...]

    @property
    def passed(self) -> bool:
        """True if no stage exceeds its regression threshold."""
        return not any(r.exceeded for r in self.regressions)

    @property
    def failed_stages(self) -> tuple[StageRegression, ...]:
        """Stages that exceeded their regression threshold."""
        return tuple(r for r in self.regressions if r.exceeded)


class RegressionDetector:
    """Compares a ``BenchmarkReport`` against stored baselines and returns a ``RegressionResult``.

    If no baseline is stored for a stage, that stage is skipped — the first run
    always passes (it establishes the baseline rather than measuring against one).
    """

    def __init__(self, store: BaselineRepository) -> None:
        self._store = store

    def check(self, report: BenchmarkReport) -> RegressionResult:
        """Evaluate ``report`` against stored baselines.

        Returns a ``RegressionResult`` whose ``passed`` property indicates whether
        CI should succeed (True) or fail (False).
        """
        stage_regressions: list[StageRegression] = []
        for stage_result in report.stages:
            baseline = self._store.fetch_latest_p95(stage_result.stage)
            if baseline is None:
                continue
            stage_regressions.append(
                StageRegression(
                    stage=stage_result.stage,
                    baseline_p95_ms=baseline,
                    current_p95_ms=stage_result.p95_ms,
                )
            )
        return RegressionResult(regressions=tuple(stage_regressions))
