"""ContinuousProfiler -- per-stage latency profiling harness, always-on in staging (V3 Ch19, Sprint-028).

Wraps every pipeline stage (Media GW -> ASM -> Preprocessing -> VAD -> STT ->
CIL -> LLM (TTFT) -> TTS (first clause) -> Playback) with automated timing
capture and computes daily p50/p95/p99 per stage, persisted through the
injected :class:`BaselineStore` port -- a real deployment backs this with
the ``performance_baselines`` Postgres table (``src.libs.repositories.
performance_baseline.PerformanceBaselineRepository``, additive migration
``0027``); Phase 1 unit tests use :class:`InMemoryBaselineStore` (Sprint-028.md's
own "Stage timing: Fixed-duration fixtures" Phase 1 mock-backend note).

Architecture: V1 Ch23 (Latency Budget); V3 Ch19 (Performance Engineering).
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import date as date_
from typing import Any, Protocol


def compute_percentile(samples: Sequence[float], percentile: float) -> float:
    """Nearest-rank percentile of ``samples`` (0 <= percentile <= 100).

    Deterministic, dependency-free (no ``numpy``/``statistics.quantiles``
    interpolation surprises) -- the same nearest-rank method used
    throughout this project's other percentile calculations.
    """
    if not samples:
        raise ValueError("compute_percentile() requires at least one sample")
    if not 0 <= percentile <= 100:
        raise ValueError(f"percentile must be within [0, 100], got {percentile}")
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    index = min(len(ordered) - 1, max(0, round(percentile / 100 * (len(ordered) - 1))))
    return ordered[index]


@dataclass(frozen=True)
class TimingResult:
    """Result of one :meth:`ContinuousProfiler.profile_stage` invocation."""

    stage_name: str
    duration_ms: float
    recorded_at: datetime
    result: Any = None
    """The wrapped callable's own return value, so instrumentation never discards it."""


@dataclass(frozen=True)
class StagePercentiles:
    """One stage's p50/p95/p99 for one calendar day -- the ``performance_baselines`` row shape."""

    stage_name: str
    recorded_date: date_
    p50_ms: float
    p95_ms: float
    p99_ms: float
    sample_count: int


class BaselineStore(Protocol):
    """Injected persistence port for daily per-stage percentiles.

    Structural (no inheritance required) -- ``PerformanceBaselineRepository``
    (Postgres-backed, Phase 2) satisfies this by method signature alone,
    same "port defined where it's consumed" precedent as
    ``src.services.cost_optimizer.tracker.ConversationUsageSource``.
    """

    def record_percentiles(self, percentiles: StagePercentiles) -> None: ...

    def percentiles_for(self, stage_name: str, recorded_date: date_) -> StagePercentiles | None: ...


class InMemoryBaselineStore:
    """Default, in-process :class:`BaselineStore` -- Phase 1 fixture-driven backend."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, date_], StagePercentiles] = {}

    def record_percentiles(self, percentiles: StagePercentiles) -> None:
        self._store[(percentiles.stage_name, percentiles.recorded_date)] = percentiles

    def percentiles_for(self, stage_name: str, recorded_date: date_) -> StagePercentiles | None:
        return self._store.get((stage_name, recorded_date))


class ContinuousProfiler:
    """Per-stage latency profiling harness -- always-on in staging (V3 Ch19).

    ``profile_stage`` is the synchronous instrumentation point: call it
    around any single stage invocation to capture its duration without
    losing the wrapped callable's return value. Samples accumulate
    in-process across a run; :meth:`flush_daily_percentiles` computes and
    persists p50/p95/p99 for everything sampled so far.
    """

    def __init__(
        self,
        baseline_store: BaselineStore | None = None,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._baseline_store = baseline_store or InMemoryBaselineStore()
        self._clock = clock
        self._samples: dict[str, list[float]] = defaultdict(list)

    def profile_stage(self, stage_name: str, fn: Callable[[], Any]) -> TimingResult:
        """Time one invocation of ``fn``, tagged as ``stage_name``.

        Returns a :class:`TimingResult` carrying both the measured
        duration and ``fn``'s own return value (``.result``).
        """
        start = self._clock()
        value = fn()
        elapsed_ms = (self._clock() - start) * 1000.0
        timing = TimingResult(
            stage_name=stage_name,
            duration_ms=elapsed_ms,
            recorded_at=datetime.now(UTC),
            result=value,
        )
        self._samples[stage_name].append(elapsed_ms)
        return timing

    def record_sample(self, stage_name: str, duration_ms: float) -> None:
        """Record an externally-measured duration for ``stage_name``.

        For stages measured out-of-process (e.g. a GPU-node HTTP probe's
        own reported latency) where wrapping a local callable isn't
        possible.
        """
        self._samples[stage_name].append(duration_ms)

    def samples_for(self, stage_name: str) -> tuple[float, ...]:
        return tuple(self._samples.get(stage_name, ()))

    def flush_daily_percentiles(self, recorded_date: date_ | None = None) -> tuple[StagePercentiles, ...]:
        """Compute and persist ``recorded_date``'s (default: today, UTC) p50/p95/p99 for every sampled stage."""
        day = recorded_date or datetime.now(UTC).date()
        computed: list[StagePercentiles] = []
        for stage_name, samples in self._samples.items():
            if not samples:
                continue
            percentiles = StagePercentiles(
                stage_name=stage_name,
                recorded_date=day,
                p50_ms=compute_percentile(samples, 50),
                p95_ms=compute_percentile(samples, 95),
                p99_ms=compute_percentile(samples, 99),
                sample_count=len(samples),
            )
            self._baseline_store.record_percentiles(percentiles)
            computed.append(percentiles)
        return tuple(computed)

    def reset(self) -> None:
        """Clear all accumulated samples (e.g. between benchmark runs in a long-lived test process)."""
        self._samples.clear()


__all__ = [
    "BaselineStore",
    "ContinuousProfiler",
    "InMemoryBaselineStore",
    "StagePercentiles",
    "TimingResult",
    "compute_percentile",
]
