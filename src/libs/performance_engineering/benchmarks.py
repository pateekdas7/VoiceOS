"""BenchmarkSuite -- per-stage fixture-driven benchmarks with baseline tracking (V3 Ch19, Sprint-028).

Runs each pipeline stage's benchmark and compares its p95 against the
stage's registered budget. CI runs this against deterministic fixture
timings (Sprint-028.md's own Phase 1 "Stage timing: Fixed-duration
fixtures" mock backend); a real deployment can inject a
:class:`StageFixtureProvider` backed by :class:`~.profiler.ContinuousProfiler`'s
own collected samples for a given environment/day.

Architecture: V1 Ch23 (Latency Budget); V3 Ch19 (Performance Engineering).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .profiler import compute_percentile

STAGE_BUDGETS_MS: Mapping[str, float] = {
    "stt": 300.0,
    "cil": 120.0,
    "llm_ttft": 350.0,
    "tts_first_clause": 750.0,  # ADR-004: revised from 250ms — Veena 3B SNAC requires min 21 tokens × 32.7ms/tok = 642ms minimum
}
"""Per-stage p95 budgets required by Sprint-028.md's own BenchmarkSuite spec
(V1 Ch23's full first-audio budget also includes endpoint=120ms/validate=40ms/
resample=30ms, which are point-in-pipeline overhead rather than a single
benchmarked stage, so they are not separate BenchmarkSuite entries)."""


@dataclass(frozen=True)
class StageBenchmarkResult:
    """One stage's benchmark outcome for a single :meth:`BenchmarkSuite.run_benchmarks` run."""

    stage_name: str
    p95_ms: float
    budget_ms: float
    sample_count: int

    @property
    def within_budget(self) -> bool:
        return self.p95_ms <= self.budget_ms


@dataclass(frozen=True)
class BenchmarkReport:
    """Output of :meth:`BenchmarkSuite.run_benchmarks` -- every benchmarked stage's result."""

    environment: str
    results: tuple[StageBenchmarkResult, ...]

    @property
    def passed(self) -> bool:
        """``True`` iff every benchmarked stage's p95 is within its budget."""
        return all(r.within_budget for r in self.results)

    def failures(self) -> tuple[StageBenchmarkResult, ...]:
        return tuple(r for r in self.results if not r.within_budget)


class StageFixtureProvider(Protocol):
    """Injected port: representative duration samples (ms) for a stage, to benchmark against."""

    def samples_for(self, stage_name: str) -> tuple[float, ...]: ...


class StaticFixtureProvider:
    """Deterministic in-memory :class:`StageFixtureProvider` -- Phase 1 fixture backend."""

    def __init__(self, fixtures: Mapping[str, tuple[float, ...]]) -> None:
        self._fixtures = dict(fixtures)

    def samples_for(self, stage_name: str) -> tuple[float, ...]:
        return self._fixtures.get(stage_name, ())


class BenchmarkSuite:
    """Per-stage baseline benchmarks: STT <= 300ms, CIL <= 120ms, LLM TTFT <= 350ms, TTS first-clause <= 750ms (ADR-004)."""

    def __init__(
        self,
        fixture_provider: StageFixtureProvider,
        *,
        budgets: Mapping[str, float] = STAGE_BUDGETS_MS,
    ) -> None:
        self._fixture_provider = fixture_provider
        self._budgets = dict(budgets)

    def run_benchmarks(self, environment: str) -> BenchmarkReport:
        """Execute every registered stage benchmark and compare its p95 against its budget.

        Stages with no fixture samples available are silently skipped
        (not benchmarked, not reported as a pass or failure) -- a
        partially-populated fixture provider is expected during early
        Phase 1 development, not an error condition.
        """
        results: list[StageBenchmarkResult] = []
        for stage_name, budget_ms in self._budgets.items():
            samples = self._fixture_provider.samples_for(stage_name)
            if not samples:
                continue
            results.append(
                StageBenchmarkResult(
                    stage_name=stage_name,
                    p95_ms=compute_percentile(samples, 95),
                    budget_ms=budget_ms,
                    sample_count=len(samples),
                )
            )
        return BenchmarkReport(environment=environment, results=tuple(results))


__all__ = [
    "STAGE_BUDGETS_MS",
    "BenchmarkReport",
    "BenchmarkSuite",
    "StageBenchmarkResult",
    "StageFixtureProvider",
    "StaticFixtureProvider",
]
