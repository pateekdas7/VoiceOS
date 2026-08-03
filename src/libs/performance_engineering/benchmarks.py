"""BenchmarkSuite — per-stage fixture-driven benchmarks with baseline tracking (V3 Ch19).

Architecture: V3 Ch19 (Performance Engineering).

Stage p95 budgets (V1 Ch23):
    stt              ≤ 300 ms
    cil              ≤ 120 ms
    llm_ttft         ≤ 350 ms
    tts_first_clause ≤ 250 ms
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, Protocol

STAGE_BUDGETS_MS: dict[str, float] = {
    "stt": 300.0,
    "cil": 120.0,
    "llm_ttft": 350.0,
    "tts_first_clause": 250.0,
}


def _percentile(sorted_values: list[float], p: float) -> float:
    """Linear-interpolation percentile of a pre-sorted list (p in 0-100 scale)."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = (p / 100.0) * (len(sorted_values) - 1)
    lo = int(idx)
    hi = lo + 1
    if hi >= len(sorted_values):
        return sorted_values[-1]
    frac = idx - lo
    return sorted_values[lo] + frac * (sorted_values[hi] - sorted_values[lo])


class StageTimingSource(Protocol):
    """Provides timing samples for a given stage — injected into BenchmarkSuite."""

    def collect(self, stage: str, n: int) -> list[float]:
        """Return ``n`` timing samples (ms) for ``stage``."""
        ...


@dataclass(frozen=True)
class StageResult:
    """Benchmark result for one pipeline stage."""

    stage: str
    p50_ms: float
    p95_ms: float
    p99_ms: float
    budget_ms: float

    @property
    def passed(self) -> bool:
        """True if p95 is within the stage's budget."""
        return self.p95_ms <= self.budget_ms

    @property
    def overage_pct(self) -> float:
        """How far above budget p95 is, as a percentage (negative = under budget)."""
        return (self.p95_ms - self.budget_ms) / self.budget_ms * 100.0


@dataclass(frozen=True)
class BenchmarkReport:
    """Aggregated benchmark result across all stages for one ``environment`` run."""

    environment: str
    timestamp: datetime
    stages: tuple[StageResult, ...]

    @property
    def passed(self) -> bool:
        """True if every stage's p95 is within its budget."""
        return all(s.passed for s in self.stages)

    def stage(self, name: str) -> StageResult | None:
        """Look up a stage result by name."""
        return next((s for s in self.stages if s.stage == name), None)


class FixtureTimingSource:
    """Deterministic fixture timings for CI/Phase 1 validation (all stages within budget)."""

    _FIXTURE_MS: ClassVar[dict[str, list[float]]] = {
        "stt": [210.0, 220.0, 230.0, 240.0, 250.0, 260.0, 270.0, 275.0, 280.0, 285.0],
        "cil": [75.0, 80.0, 85.0, 88.0, 90.0, 95.0, 98.0, 100.0, 105.0, 110.0],
        "llm_ttft": [240.0, 255.0, 265.0, 275.0, 285.0, 295.0, 305.0, 315.0, 325.0, 335.0],
        "tts_first_clause": [175.0, 182.0, 188.0, 192.0, 196.0, 200.0, 205.0, 210.0, 215.0, 238.0],
    }

    def collect(self, stage: str, n: int) -> list[float]:
        base = self._FIXTURE_MS.get(stage, [100.0])
        repeated = (base * ((n // len(base)) + 1))[:n]
        return repeated


class RegressionFixtureTimingSource:
    """Fixture timings that simulate a 15 % STT regression — used to prove the gate fails."""

    _BASE: ClassVar[FixtureTimingSource] = FixtureTimingSource()
    _STT_REGRESSION_FACTOR: ClassVar[float] = 1.15

    def collect(self, stage: str, n: int) -> list[float]:
        base = self._BASE.collect(stage, n)
        if stage == "stt":
            return [ms * self._STT_REGRESSION_FACTOR for ms in base]
        return base


class BenchmarkSuite:
    """Per-stage benchmark runner that compares observed p95 against V1 Ch23 budgets.

    Inject a ``StageTimingSource`` to decouple the benchmark from live services:
    - ``FixtureTimingSource`` for CI / Phase 1 (deterministic, always green)
    - ``RegressionFixtureTimingSource`` to prove the regression gate fires
    - A live source in Phase 2 that calls the real GPU services
    """

    def __init__(self, source: StageTimingSource, n_samples: int = 10) -> None:
        self._source = source
        self._n = n_samples

    def run_benchmarks(self, environment: str = "ci") -> BenchmarkReport:
        """Run benchmarks for every stage and return a ``BenchmarkReport``."""
        results: list[StageResult] = []
        for stage_name, budget_ms in STAGE_BUDGETS_MS.items():
            timings = self._source.collect(stage_name, self._n)
            results.append(self._evaluate_stage(stage_name, timings, budget_ms))
        return BenchmarkReport(
            environment=environment,
            timestamp=datetime.now(tz=UTC),
            stages=tuple(results),
        )

    def _evaluate_stage(self, stage: str, timings_ms: list[float], budget_ms: float) -> StageResult:
        sorted_t = sorted(timings_ms)
        return StageResult(
            stage=stage,
            p50_ms=_percentile(sorted_t, 50),
            p95_ms=_percentile(sorted_t, 95),
            p99_ms=_percentile(sorted_t, 99),
            budget_ms=budget_ms,
        )
